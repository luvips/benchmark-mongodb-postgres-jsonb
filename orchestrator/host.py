"""Operaciones sobre Docker y el host Linux/WSL2."""
import json
import shutil
import subprocess
import time
from pathlib import Path

try:
    import psycopg
    import pymongo
except ImportError:
    psycopg = None
    pymongo = None

from benchmark_runner.config import CONTAINERS, MONGO_URI, PG_DSN, ROOT, STATE_DIR

ALPINE = "alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f"
TELEMETRY = ("cadvisor", "node-exporter", "prometheus")
ENGINE_READY_TIMEOUT_S = 240
CPU_SYSFS = "/sys/devices/system/cpu/cpu*/cpufreq"


class Fault(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, str(detail)[-4000:]


def _sh(cmd, reason, timeout=180):
    try:
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise Fault(reason, exc) from exc
    if out.returncode != 0:
        raise Fault(reason, out.stdout + out.stderr)
    return out.stdout


def compose(*args, profile="A", reason="compose_failed"):
    return _sh(["docker", "compose", "--env-file", f"profiles/{profile}.env", *args], reason)


def start_telemetry():
    compose("up", "-d", *TELEMETRY, reason="telemetry_start_failed")


def stop_telemetry():
    compose("stop", *TELEMETRY)


def recreate_telemetry():
    compose("up", "-d", "--force-recreate", *TELEMETRY, reason="telemetry_start_failed")


def stop_engines():
    compose("stop", "-t", "60", *CONTAINERS)
    compose("rm", "-f", *CONTAINERS)


def start_engine(engine, profile):
    compose("up", "-d", "--force-recreate", "--no-deps", engine, profile=profile, reason="engine_start_failed")


def wait_ready(engine, timeout=ENGINE_READY_TIMEOUT_S):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            if engine == "mongodb":
                with pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000) as client:
                    client.admin.command("ping")
            else:
                with psycopg.connect(PG_DSN, connect_timeout=2, autocommit=True) as conn:
                    conn.execute("SELECT 1")
            return
        except Exception as exc:
            last = exc
            time.sleep(1)
    raise Fault("engine_not_ready", f"{engine}: {last}")


def privileged(script, reason):
    _sh(["docker", "run", "--rm", "--privileged", ALPINE, "sh", "-c", script],
        reason, timeout=120)


def drop_caches():
    privileged("sync; echo 3 > /proc/sys/vm/drop_caches", "drop_caches_failed")


def _energy_file():
    return STATE_DIR / "energy.json"


def energy_performance():
    """Fija gobernador de CPU en performance si cpufreq está disponible (ignora en WSL2/VM)."""
    cpu0 = Path("/sys/devices/system/cpu/cpu0/cpufreq")
    if not (cpu0 / "scaling_governor").exists():
        return
    if not _energy_file().exists():
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        epp_file = cpu0 / "energy_performance_preference"
        _energy_file().write_text(json.dumps({
            "governor": (cpu0 / "scaling_governor").read_text().strip(),
            "epp": epp_file.read_text().strip() if epp_file.exists() else "",
        }), encoding="utf-8")
    privileged(f"for f in {CPU_SYSFS}/scaling_governor; do [ -f \"$f\" ] && echo performance > \"$f\"; done", "energy_failed")


def energy_restore():
    if not _energy_file().exists():
        return
    saved = json.loads(_energy_file().read_text(encoding="utf-8"))
    cmd = f"for f in {CPU_SYSFS}/scaling_governor; do [ -f \"$f\" ] && echo {saved['governor']} > \"$f\"; done"
    if saved.get("epp"):
        cmd += f"; for f in {CPU_SYSFS}/energy_performance_preference; do [ -f \"$f\" ] && echo {saved['epp']} > \"$f\"; done"
    try:
        privileged(cmd, "energy_failed")
    finally:
        _energy_file().unlink(missing_ok=True)


def disk_free_gib():
    return shutil.disk_usage("/var/lib/docker" if Path("/var/lib/docker").exists() else ROOT).free / 2**30


def mem_available_gib():
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 8.0
    for line in meminfo.read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 2**20
    return 0.0
