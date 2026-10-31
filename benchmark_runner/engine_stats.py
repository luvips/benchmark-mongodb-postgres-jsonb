"""Contadores internos, metadatos de versión y señales de spill/OOM de cada motor."""
import subprocess

try:
    import psycopg
    import pymongo
except ImportError:
    psycopg = None
    pymongo = None

from .config import CONTAINERS, MONGO_DB, MONGO_URI, PG_DSN
from .telemetry import container_logs

# En una carga 100% de lectura, escribir >= 32 MiB desde el cgroup en la ventana de medición
# indica creación de archivos temporales (incluso si un OOM posterior reinicia pg_stat_database).
PG_SPILL_WRITE_BYTES = 32 * 2**20


def engine_version(engine):
    try:
        if engine == "mongodb":
            with pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) as client:
                info = client.admin.command("buildInfo")
            return f"MongoDB {info.get('version', 'unknown')}"
        with psycopg.connect(PG_DSN, autocommit=True, connect_timeout=5) as conn:
            ver = conn.execute("SHOW server_version").fetchone()[0]
        return f"PostgreSQL {ver}"
    except Exception:
        return "unknown"


def counters(engine):
    if engine == "mongodb":
        with pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) as client:
            query = client.admin.command("serverStatus")["metrics"]["query"]
        return {
            "sort_spills": int(query.get("sort", {}).get("spillToDisk", 0)),
            "group_spills": int(query.get("group", {}).get("spills", 0)),
        }
    with psycopg.connect(PG_DSN, autocommit=True, connect_timeout=5) as conn:
        files, size = conn.execute(
            "SELECT temp_files, temp_bytes FROM pg_stat_database WHERE datname = current_database()"
        ).fetchone()
    return {"temp_files": int(files), "temp_bytes": int(size)}


def row_count(engine):
    if engine == "mongodb":
        with pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) as client:
            return client[MONGO_DB]["orders"].estimated_document_count()
    with psycopg.connect(PG_DSN, autocommit=True, connect_timeout=5) as conn:
        return int(conn.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = 'orders'").fetchone()[0])


def spill_evidence(engine, before, after, since_epoch, written_bytes=0):
    """Triple verificación de spill a disco: contadores internos, logs del contenedor y delta de io.stat."""
    marker = '"usedDisk":true' if engine == "mongodb" else "temporary file:"
    lines = [l for l in container_logs(CONTAINERS[engine], since_epoch).splitlines() if marker in l]
    grew = bool(after) and any(after[k] > before.get(k, 0) for k in after if k != "temp_bytes")
    wrote = engine == "postgresql" and written_bytes >= PG_SPILL_WRITE_BYTES
    if wrote:
        lines.append(f"cgroup io.stat window delta: {written_bytes} bytes escritos con carga de solo lectura")
    return grew or wrote or bool(lines), lines


def oom_evidence(engine, since_epoch, state):
    """Reúne evidencia del kernel y del motor antes de recrear el contenedor."""
    engine_lines = [
        l for l in container_logs(CONTAINERS[engine], since_epoch).splitlines()
        if "signal 9" in l or "out of memory" in l.lower() or "killed" in l.lower()
    ]
    kernel_lines = []
    try:
        journal = subprocess.run(
            ["journalctl", "-k", "--since", f"@{int(since_epoch)}", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        kernel_lines = [l for l in journal.splitlines() if "Memory cgroup out of memory" in l or "Killed process" in l]
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    head = [
        f"docker inspect OOMKilled={state.get('OOMKilled')}",
        f"kernel: {len(kernel_lines)} eventos de OOM; motor: {len(engine_lines)} líneas",
    ]
    return "\n".join(head + kernel_lines[:20] + engine_lines[:20]) + "\n"
