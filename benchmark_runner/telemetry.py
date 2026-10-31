"""Lecturas de Prometheus y muestreo directo de archivos cgroups v2 del contenedor.

Mejoras técnicas clave:
1. Soporta tanto driver `systemd` (`/system.slice/docker-<cid>.scope`) como `cgroupfs`
   (`/docker/<cid>`), compatible con Linux nativo y WSL2.
2. En `Sampler`, calcula `written_bytes` estrictamente entre `window_start` y `window_end`
   (los 300 s de medición), excluyendo warmup y cooldown.
3. Rastrea los máximos dentro de la ventana de medición (`max_anon`, `max_shmem`, `max_file`,
   `max_current` y `max_engine` = máx. por muestra de anon + shmem) para que un reinicio por
   OOM al final de la corrida no reporte la memoria final en lugar del pico real.
   Nota: `memory.peak` del kernel es el máximo desde la creación del cgroup (incluye warmup)
   y cuenta page cache (`file`); por eso la huella de M3 usa `max_engine`, no `memory.peak`.
4. Consulta también `container_cpu_usage_seconds_total` en Prometheus para el cálculo de M3.
"""
import csv
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import IDLE_SIBLING_CPUS, PROMETHEUS_URL

METRIC_SOURCE = os.environ.get("BENCH_METRIC_SOURCE", "prometheus")


def prom_query(expr, at):
    params = urllib.parse.urlencode({"query": expr, "time": at})
    with urllib.request.urlopen(f"{PROMETHEUS_URL}/api/v1/query?{params}", timeout=10) as r:
        result = json.load(r)["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def prom_selector(cid):
    # Filtra por ID completo del contenedor mediante regex para cubrir tanto
    # /system.slice/docker-<cid>.scope (systemd) como /docker/<cid> (cgroupfs / WSL2)
    return f'{{id=~".*{cid}.*"}}'


def prom_metrics(cid, t0, t1):
    """Devuelve (throttled_s, cpu_usage_s, max_rss_bytes, max_ws_bytes, samples) en [t0, t1]."""
    window = max(1, round(t1 - t0))
    sel = prom_selector(cid)
    thr_start = prom_query(f"container_cpu_cfs_throttled_seconds_total{sel}", t0) or 0.0
    thr_end = prom_query(f"container_cpu_cfs_throttled_seconds_total{sel}", t1)
    cpu_start = prom_query(f"container_cpu_usage_seconds_total{sel}", t0) or 0.0
    cpu_end = prom_query(f"container_cpu_usage_seconds_total{sel}", t1)
    rss = prom_query(f"max_over_time(container_memory_rss{sel}[{window}s])", t1)
    ws = prom_query(f"max_over_time(container_memory_working_set_bytes{sel}[{window}s])", t1)
    samples = prom_query(f"count_over_time(container_memory_rss{sel}[{window}s])", t1)
    throttled = None if thr_end is None else max(0.0, thr_end - thr_start)
    cpu_usage = None if cpu_end is None else max(0.0, cpu_end - cpu_start)
    return throttled, cpu_usage, rss, ws, samples or 0


def sources_agree(prometheus_s, cgroup_s):
    """Verifica coherencia entre Prometheus y lectura directa de cpu.stat del cgroup."""
    if prometheus_s is None or cgroup_s is None:
        return True
    return abs(prometheus_s - cgroup_s) <= max(5.0, 0.15 * max(prometheus_s, cgroup_s))


def container_id(name):
    out = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", name], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def container_state(name):
    out = subprocess.run(["docker", "inspect", "-f", "{{json .State}}", name], capture_output=True, text=True)
    return json.loads(out.stdout) if out.returncode == 0 else {}


def container_host_config(name):
    out = subprocess.run(
        ["docker", "inspect", "-f",
         '{"NanoCpus":{{.HostConfig.NanoCpus}},"Memory":{{.HostConfig.Memory}},"MemorySwap":{{.HostConfig.MemorySwap}},"CpusetCpus":"{{.HostConfig.CpusetCpus}}","Image":"{{.Config.Image}}"}',
         name],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return {}
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {}


def container_logs(name, since_epoch):
    out = subprocess.run(["docker", "logs", "--since", str(int(since_epoch)), name],
                         capture_output=True, text=True, errors="replace")
    return out.stdout + out.stderr


def _kv_file(path: Path):
    return {k: int(v) for k, v in (line.split() for line in path.read_text().splitlines())}


def write_bytes(path: Path):
    if not path.exists():
        return 0
    fields = (f.partition("=") for line in path.read_text().splitlines() for f in line.split()[1:])
    return sum(int(value) for key, _, value in fields if key == "wbytes")


CGROUP_ROOTS = (
    Path("/sys/fs/cgroup"),
    Path(r"\\wsl.localhost\docker-desktop\sys\fs\cgroup"),
)
PROC_ROOTS = (
    Path("/proc"),
    Path(r"\\wsl.localhost\docker-desktop\proc"),
)


class Cgroup:
    def __init__(self, cid):
        candidates = [
            root / rel
            for root in CGROUP_ROOTS
            for rel in (f"system.slice/docker-{cid}.scope", f"docker/{cid}")
        ]
        self.path = next((p for p in candidates if p.exists()), candidates[0])

    def exists(self):
        return self.path.exists()

    def snapshot(self):
        mem = _kv_file(self.path / "memory.stat")
        cpu = _kv_file(self.path / "cpu.stat")
        peak_file = self.path / "memory.peak"
        current = int((self.path / "memory.current").read_text())
        peak = int(peak_file.read_text()) if peak_file.exists() else current
        return {
            "anon": mem.get("anon", 0),
            "shmem": mem.get("shmem", 0),
            "file": mem.get("file", 0),
            "usage_usec": cpu.get("usage_usec", 0),
            "throttled_usec": cpu.get("throttled_usec", 0),
            "nr_throttled": cpu.get("nr_throttled", 0),
            "current": current,
            "peak": peak,
            "oom_kill": _kv_file(self.path / "memory.events").get("oom_kill", 0),
            "wbytes": write_bytes(self.path / "io.stat"),
        }


def _cpu_times(cpus):
    proc_stat = next((r / "stat" for r in PROC_ROOTS if (r / "stat").exists()), None)
    if proc_stat is None:
        return 0, 1
    busy = total = 0
    for line in proc_stat.read_text().splitlines():
        head, *fields = line.split()
        if head.startswith("cpu") and head != "cpu" and int(head[3:]) in cpus:
            values = [int(v) for v in fields[:8]]
            total += sum(values)
            busy += sum(values) - values[3] - values[4]
    return busy, total


def host_busy_pct(cpus, seconds=2):
    b0, t0 = _cpu_times(cpus)
    time.sleep(seconds)
    b1, t1 = _cpu_times(cpus)
    return 100 * (b1 - b0) / max(1, t1 - t0)


class Sampler(threading.Thread):
    """Muestreador de 1 s directo sobre cgroups v2; preserva picos en ventana aunque ocurra OOM."""

    FIELDS = [
        "t", "anon", "shmem", "file", "current", "usage_usec",
        "throttled_usec", "nr_throttled", "peak", "wbytes",
    ]

    def __init__(self, cgroup, out_path, clock=time.monotonic):
        super().__init__(daemon=True)
        self.cgroup, self.out_path = cgroup, out_path
        self.clock = clock
        self._prev_t = None
        self.halt = threading.Event()
        self.first = self.last = {}
        self.window_start = self.window_end = None
        self.mark_start = self.mark_end = False
        self.b0, self.t0 = self.b1, self.t1 = (0, 0)
        self.window_max_anon = 0
        self.window_max_shmem = 0
        self.window_max_current = 0
        self.window_max_peak = 0
        self.window_max_file = 0
        self.window_max_engine = 0
        self.window_sum_engine = 0.0   # integral de (anon + shmem) en la ventana, bytes·s
        self.window_seconds = 0.0      # suma de los Δt usados en la integral
        self.window_samples = 0

    def mark(self, which):
        setattr(self, f"mark_{which}", True)

    def run(self):
        with open(self.out_path, "w", newline="", encoding="utf-8") as f:
            out = csv.DictWriter(f, fieldnames=self.FIELDS, extrasaction="ignore")
            out.writeheader()
            while not self.halt.wait(1):
                try:
                    snap = self.cgroup.snapshot()
                except (OSError, KeyError, ValueError):
                    continue
                now = self.clock()
                # Δt real desde la muestra anterior (≈1 s): absorbe la deriva de wait(1) + lectura y
                # cubre segundos sin muestra (lectura fallida) en lugar de suponer Δt = 1 s fijo.
                dt = 1.0 if self._prev_t is None else now - self._prev_t
                self._prev_t = now
                self.first = self.first or snap
                self.last = snap
                if self.mark_start and not self.window_start:
                    self.window_start = snap
                    self.b0, self.t0 = _cpu_times(IDLE_SIBLING_CPUS)
                if self.window_start and not self.window_end:
                    engine_bytes = snap["anon"] + snap["shmem"]
                    self.window_max_anon = max(self.window_max_anon, snap["anon"])
                    self.window_max_shmem = max(self.window_max_shmem, snap["shmem"])
                    self.window_max_current = max(self.window_max_current, snap["current"])
                    self.window_max_peak = max(self.window_max_peak, snap["peak"])
                    self.window_max_file = max(self.window_max_file, snap["file"])
                    self.window_max_engine = max(self.window_max_engine, engine_bytes)
                    self.window_sum_engine += engine_bytes * dt
                    self.window_seconds += dt
                    self.window_samples += 1
                if self.mark_end and not self.window_end:
                    self.window_end = snap
                    self.b1, self.t1 = _cpu_times(IDLE_SIBLING_CPUS)
                out.writerow({"t": time.time(), **snap})
                f.flush()

    def stop(self):
        self.halt.set()
        self.join(5)

    def written_bytes(self):
        """Bytes escritos en bloque estrictamente durante la ventana de medición [t0, t1]."""
        start = self.window_start or self.first
        end = self.window_end or self.last
        if not (start and end):
            return 0
        return max(0, end.get("wbytes", 0) - start.get("wbytes", 0))

    def idle_sibling_busy_pct(self):
        return 100 * (self.b1 - self.b0) / max(1, self.t1 - self.t0)

    def cpu_seconds(self):
        start = self.window_start or self.first
        end = self.window_end or self.last
        if not (start and end):
            return None
        return max(0.0, (end["usage_usec"] - start["usage_usec"]) / 1e6)

    def throttled_seconds(self):
        start = self.window_start or self.first
        end = self.window_end or self.last
        if not (start and end):
            return None
        return max(0.0, (end["throttled_usec"] - start["throttled_usec"]) / 1e6)
