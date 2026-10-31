"""Registro estructurado de incidencias de infraestructura."""
import json
import os
import subprocess
import time

from benchmark_runner.config import ROOT

AGENT_TIMEOUT_S = 900
MAX_ATTEMPTS = 2
# Tope operativo (no afecta a la medición). Subido de 4 a 12 tras observar que Docker Desktop/WSL2
# se degrada tras ~1.5 h de uso continuo y agotaba el presupuesto original en una sola noche.
MAX_INCIDENTS = 12


def _capture(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=15)
        return (out.stdout + out.stderr)[-3000:]
    except Exception as exc:
        return str(exc)


def open_incident(run, fault):
    incident_id = time.strftime("%Y%m%d-%H%M%S")
    directory = ROOT / "incidents" / incident_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "incident.json").write_text(json.dumps({
        "id": incident_id,
        "run": run,
        "reason": fault.reason,
        "detail": fault.detail,
        "docker_ps": _capture(["docker", "ps", "-a", "--format", "{{.Names}} {{.Status}}"]),
        "disk": _capture(["df", "-h", "/"]),
        "memory": _capture(["free", "-h"]),
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    return incident_id, directory


def spawn_agent(incident_id, directory, attempt):
    override = os.environ.get("BENCH_AGENT_CMD")
    if not override:
        return {"exit": "no_agent_configured", "seconds": 0}
    log = directory / f"agent-{attempt}.json"
    started = time.time()
    try:
        with log.open("w", encoding="utf-8") as out:
            code = subprocess.run(
                ["sh", "-c", override, "agent", incident_id],
                cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, timeout=AGENT_TIMEOUT_S,
            ).returncode
    except subprocess.TimeoutExpired:
        code = "timeout"
    except OSError as exc:
        code = f"spawn_error: {exc}"
    return {"exit": code, "seconds": round(time.time() - started)}


def commit_fix(incident_id):
    subprocess.run(["git", "add", "--", f"incidents/{incident_id}", "orchestrator", "benchmark_runner"], cwd=ROOT)
    message = f"fix(campaign): resolve incident {incident_id}\n"
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=ROOT)
