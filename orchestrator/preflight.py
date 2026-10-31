"""Comprobaciones pre-vuelo antes de iniciar o reanudar la campaña."""
import os
import subprocess
from pathlib import Path

from benchmark_runner.config import (DATA_PATH, DB_CPUS, IDLE_SIBLING_CPUS, REHEARSAL, ROOT,
                                     RUNNER_CPUS, STATE_DIR)
from benchmark_runner.telemetry import host_busy_pct

from . import host, manifest

MIN_DISK_GIB = 10
MIN_MEM_GIB = 6
MAX_BUSY_PCT = 25


def _ok(cmd):
    return subprocess.run(cmd, capture_output=True).returncode == 0


def _cpus_ok():
    """Los cpusets de docker-compose.yml y config.py asumen 12 CPUs lógicas (6c/12t)."""
    available = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else set(range(os.cpu_count() or 0))
    needed = DB_CPUS | RUNNER_CPUS | IDLE_SIBLING_CPUS
    missing = sorted(needed - available)
    return not missing, f"faltan CPUs lógicas {missing}; ajusta cpusets (BENCH_CPUSET_*) y config.py"


def _images_ok():
    """Todas las imágenes fijadas por digest deben existir localmente (docker compose pull)."""
    out = subprocess.run(["docker", "compose", "--env-file", "profiles/A.env", "config", "--images"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        return False, "docker compose config falló"
    missing = [img for img in out.stdout.split()
               if subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode != 0]
    return not missing, "ejecuta docker compose pull; faltan: " + ", ".join(missing)


def checks():
    disk, mem = host.disk_free_gib(), host.mem_available_gib()
    result = [
        ("Docker activo", _ok(["docker", "info"]), ""),
        ("docker compose", _ok(["docker", "compose", "version"]), "instala Docker Compose v2"),
        ("cgroups v2", Path("/sys/fs/cgroup/cgroup.controllers").exists(), "ejecuta dentro de Linux/WSL2 con cgroups v2"),
        ("Dataset cargado", (STATE_DIR / "dataset.json").exists(), "ejecuta ./run_benchmarks.sh load-data"),
        (f"Disco libre >= {MIN_DISK_GIB} GiB", disk >= MIN_DISK_GIB, f"{disk:.1f} GiB"),
        ("CPUs lógicas para cpusets", *_cpus_ok()),
        ("Imágenes con digest disponibles", *_images_ok()),
    ]
    if (STATE_DIR / "manifest.json").exists():
        drift = manifest.changed(STATE_DIR / "manifest.json", DATA_PATH)
        result.append(("Manifiesto intacto", not drift, ", ".join(drift)))
    if not REHEARSAL:
        busy = host_busy_pct(set(range(os.cpu_count() or 1)))
        result += [
            (f"RAM disponible >= {MIN_MEM_GIB} GiB", mem >= MIN_MEM_GIB, f"{mem:.1f} GiB"),
            (f"Host en reposo (< {MAX_BUSY_PCT} % CPU)", busy < MAX_BUSY_PCT, f"{busy:.0f} % ocupado"),
        ]
    return result
