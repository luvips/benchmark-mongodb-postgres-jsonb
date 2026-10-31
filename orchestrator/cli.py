"""CLI de run_benchmarks.sh con soporte para Linux nativo y WSL2, más agregación estadística."""
import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from benchmark_runner.config import (CONCURRENCIES, DATA_PATH, ENGINES, MIN_P99_SAMPLES, PROFILES,
                                     QUERIES, REHEARSAL, RESULTS_DIR, ROOT, STATE_DIR, VARIANTS)

# Consultas adicionales solo para la celda piloto (no forman parte de la matriz)
PILOT_ONLY_QUERIES = {"Q1_FLOAT8": "postgresql"}

from . import dataload, explain, host, manifest, preflight
from .state import Journal, build_plan, derive

SEED = 20261030
UNIT = "bench-rehearsal" if REHEARSAL else "bench-campaign"


def journal():
    return Journal(STATE_DIR / "journal.jsonl")


def plan():
    path = STATE_DIR / "plan.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _pid_file():
    return STATE_DIR / "campaign.pid"


def service_active():
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", UNIT], capture_output=True).returncode == 0:
        return True
    pf = _pid_file()
    if pf.exists():
        try:
            pid = int(pf.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, OSError):
            return False
    return False


def launch():
    log = ROOT / STATE_DIR.name / "campaign.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    py_bin = ROOT / ".venv/bin/python"
    if not py_bin.exists():
        py_bin = Path(sys.executable)

    # Intenta systemd-run --user primero; si no está disponible (ej. WSL2 sin systemd user bus),
    # usa proceso background desacoplado con archivo PID.
    env = [f"--setenv={k}={v}" for k, v in os.environ.items() if k.startswith("BENCH_") or k in ("PATH", "HOME")]
    subprocess.run(["systemctl", "--user", "reset-failed", UNIT], capture_output=True)
    res = subprocess.run([
        "systemd-run", "--user", f"--unit={UNIT}", "--collect", "--same-dir", *env,
        f"--property=StandardOutput=append:{log}", f"--property=StandardError=append:{log}",
        str(py_bin), "-m", "orchestrator.campaign",
    ], capture_output=True)
    if res.returncode == 0:
        return
    with log.open("a", encoding="utf-8") as out:
        proc = subprocess.Popen(
            [str(py_bin), "-m", "orchestrator.campaign"],
            cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    _pid_file().write_text(str(proc.pid), encoding="utf-8")


def require_clean_host():
    failed = [c for c in preflight.checks() if not c[1]]
    for name, _, detail in failed:
        print(f"✗ {name}: {detail}")
    if failed:
        sys.exit(1)


def save_host_spec():
    """Registra especificaciones del host/VM (incluyendo detección explícita de WSL2) para la Sección 1 del reporte."""
    commands = [
        "uname -srm",
        "cat /proc/version",
        "lscpu",
        "free -h",
        "swapon --show",
        "docker version",
        "docker compose version",
        "docker info --format 'cgroup v{{.CgroupVersion}}, driver {{.CgroupDriver}}, storage {{.Driver}}'",
        "docker compose config --images",
    ]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with (RESULTS_DIR / "host_spec.txt").open("w", encoding="utf-8") as out:
        proc_ver = Path("/proc/version").read_text(encoding="utf-8", errors="ignore") if Path("/proc/version").exists() else ""
        if "microsoft" in proc_ver.lower() or "wsl" in proc_ver.lower():
            out.write("# NOTA METODOLÓGICA: Entorno ejecutado bajo WSL2 (Windows Subsystem for Linux 2).\n")
            out.write("# Node Exporter y drop_caches operan sobre el kernel de la VM Linux de WSL2.\n\n")
        for command in commands:
            result = subprocess.run(command, shell=True, cwd=ROOT, capture_output=True, text=True)
            out.write(f"$ {command}\n{result.stdout}{result.stderr}\n")


def cmd_start(args):
    if plan() and journal().read():
        sys.exit("Ya existe una campaña. Usa resume.")
    require_clean_host()
    repetitions = 1 if REHEARSAL else 3
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "plan.json").write_text(json.dumps(build_plan(SEED, repetitions), indent=1), encoding="utf-8")
    manifest.save(STATE_DIR / "manifest.json", DATA_PATH)
    save_host_spec()
    launch()
    print(f"Campaña iniciada: {len(plan())} corridas.")


def cmd_resume(args):
    if service_active():
        sys.exit("La campaña ya está corriendo.")
    if derive(plan(), journal().read()).state == "done":
        sys.exit("La campaña ya terminó.")
    require_clean_host()
    (STATE_DIR / "control").unlink(missing_ok=True)
    launch()
    print("Campaña reanudada.")


def cmd_pause(args):
    if not service_active():
        sys.exit("No hay campaña en ejecución.")
    (STATE_DIR / "control").write_text("pause_now" if args.now else "pause", encoding="utf-8")
    print("Abortando la corrida actual." if args.now else "Se pausará al terminar la corrida actual.")


def cmd_status(args):
    events, runs = journal().read(), plan()
    d = derive(runs, events)
    starts = {}
    durations = []
    current = None
    for e in events:
        if e["event"] == "run_started":
            starts[e["run"]] = e["ts"]
            current = e["run"]
        elif e["event"] == "run_finished" and e["run"] in starts:
            durations.append(e["ts"] - starts[e["run"]])
            current = None
    average = sum(durations) / len(durations) if durations else 465
    state = d.state
    if state == "running" and not service_active():
        state = "interrumpida (usa resume)"
    print(f"Estado:      {state}")
    print(f"Progreso:    {len(d.finished)}/{len(runs)} corridas")
    print(f"Restante:    ≈{len(d.pending) * average / 3600:.1f} h")
    if current and service_active():
        print(f"Corrida:     {current}")
    if d.suspects:
        print(f"Sospechosas: {len(d.suspects)}")
    print(f"Incidencias: {d.incidents}")
    if d.halt_reason:
        print(f"Motivo:      {d.halt_reason}")


def cmd_doctor(args):
    for name, ok, detail in preflight.checks():
        print(f"{'✓' if ok else '✗'} {name}" + (f": {detail}" if not ok and detail else ""))
    print(subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
                         capture_output=True, text=True).stdout)


def cmd_wait_end(args):
    while True:
        state = derive(plan(), journal().read()).state
        if state in ("done", "halted"):
            print(state)
            return
        if state == "running" and not service_active():
            time.sleep(10)
            if derive(plan(), journal().read()).state == "running" and not service_active():
                print("interrupted")
                return
        time.sleep(30)


def cmd_validate(args):
    report = dataload.validate(Path(args.file) if args.file else DATA_PATH)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    if report["errors"]:
        sys.exit(1)


def load(path):
    report = dataload.validate(path)
    if report["errors"]:
        sys.exit("Dataset inválido:\n" + "\n".join(report["errors"]))
    host.stop_engines()
    host.start_engine("mongodb", "A")
    host.wait_ready("mongodb")
    mongo = dataload.load_mongo(path)
    host.stop_engines()
    host.start_engine("postgresql", "A")
    host.wait_ready("postgresql")
    pg = dataload.load_pg(path)
    host.stop_engines()
    if not dataload.same_dataset(mongo, pg):
        sys.exit(f"Los motores no coinciden: {mongo} vs {pg}")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "dataset.json").write_text(json.dumps({
        **pg,
        "avg_doc_bytes": report["avg_doc_bytes"],
        "source": str(path),
    }, indent=1), encoding="utf-8")
    print(json.dumps({"mongodb": mongo, "postgresql": pg}, indent=1))


def cmd_load(args):
    load(Path(args.file).resolve() if args.file else DATA_PATH)


def cmd_repair_telemetry(args):
    host.recreate_telemetry()


def cmd_repair_engines(args):
    host.stop_engines()


def cmd_repair_reload(args):
    load(DATA_PATH)


def cmd_run_one(args):
    """Celda piloto independiente: ejecuta ciclo completo + EXPLAIN + CSV piloto + meta.json."""
    from benchmark_runner.csvout import append_row
    from benchmark_runner.runner import execute

    only = PILOT_ONLY_QUERIES.get(args.query)
    if only and args.engine != only:
        sys.exit(f"{args.query} solo existe para --engine {only} (micro-comparativa ::numeric vs ::float8)")
    run = {
        "id": f"{args.engine}-{args.profile}-{args.query}-c{args.concurrency}-{args.variant}-{int(time.time())}",
        "engine": args.engine,
        "profile": args.profile,
        "query": args.query,
        "concurrency": args.concurrency,
        "rep": 1,
        "variant": args.variant,
    }
    out = RESULTS_DIR / "pilot"
    host.start_telemetry()
    host.stop_engines()
    host.drop_caches()
    host.start_engine(args.engine, args.profile)
    host.wait_ready(args.engine)
    outcome = execute(run, out / run["id"], lambda: False)
    (out / f"{run['id']}.plan.txt").write_text(
        explain.explain(args.engine, args.query, args.variant), encoding="utf-8"
    )
    host.stop_engines()
    append_row(out / f"pilot-{run['id']}.csv", outcome.row)
    print(json.dumps({**outcome.row, "fault": outcome.fault, "reason": outcome.reason}, indent=1))


def _meta_n(row):
    """Lee n_completed de results/raw/<run_id>/meta.json a partir de una fila del CSV."""
    letter = {v: k for k, v in PROFILES.items()}.get(row["profile"], row["profile"])
    query = row["query_id"]
    base = query.split("_", 1)[0]
    suffix = "" if query == base else "-" + query.split("_", 1)[1]
    run_id = f"{row['engine']}-{letter}-{base}-c{row['concurrency']}-r{row['run_number']}{suffix}"
    try:
        meta = json.loads((RESULTS_DIR / "raw" / run_id / "meta.json").read_text(encoding="utf-8"))
        return meta.get("n_completed")
    except (OSError, ValueError):
        return None


def _med_range(vals):
    clean = [float(v) for v in vals if v is not None and v != ""]
    if not clean:
        return None, None, None
    return round(statistics.median(clean), 3), round(min(clean), 3), round(max(clean), 3)


def cmd_summarize(args):
    """Consolida las 3 repeticiones por celda reportando mediana y rango [min, max] + métricas M3."""
    csv_file = Path(args.csv) if args.csv else (RESULTS_DIR / "mediciones.csv")
    if not csv_file.exists():
        sys.exit(f"No existe el archivo CSV: {csv_file}")
    groups = defaultdict(list)
    with csv_file.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["engine"], row["profile"], row["query_id"], int(row["concurrency"]))
            groups[key].append(row)

    summary_rows = []
    for (engine, profile, query_id, conc), rows in sorted(groups.items()):
        qps_m, qps_lo, qps_hi = _med_range([r["throughput_qps"] for r in rows])
        p50_m, _, _ = _med_range([r["latency_p50_ms"] for r in rows])
        p95_m, _, _ = _med_range([r["latency_p95_ms"] for r in rows])
        p99_m, p99_lo, p99_hi = _med_range([r["latency_p99_ms"] for r in rows])
        thr_m, _, _ = _med_range([r["cpu_throttled_seconds"] for r in rows])
        rss_m, _, _ = _med_range([r["max_rss_mb"] for r in rows])
        spills = sum(1 for r in rows if r["disk_spill"] == "true")
        statuses = [r["status"] for r in rows]
        with_latency = [r for r in rows if r["latency_p99_ms"] not in ("", None)]
        ns = [n for n in (_meta_n(r) for r in with_latency) if n is not None]
        summary_rows.append({
            "engine": engine,
            "profile": profile,
            "query_id": query_id,
            "concurrency": conc,
            "runs": len(rows),
            "median_qps": qps_m,
            "range_qps": [qps_lo, qps_hi],
            "median_p50_ms": p50_m,
            "median_p95_ms": p95_m,
            "median_p99_ms": p99_m,
            "range_p99_ms": [p99_lo, p99_hi],
            "median_throttled_s": thr_m,
            "median_max_rss_mb": rss_m,
            "runs_with_latency": len(with_latency),
            "min_n_completed": min(ns) if ns else None,
            "p99_reliable": bool(ns) and min(ns) >= MIN_P99_SAMPLES,
            "spill_runs": spills,
            "statuses": statuses,
        })
    print(json.dumps(summary_rows, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(prog="run_benchmarks.sh")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("start", cmd_start),
        ("resume", cmd_resume),
        ("status", cmd_status),
        ("doctor", cmd_doctor),
        ("wait-end", cmd_wait_end),
        ("repair-telemetry", cmd_repair_telemetry),
        ("repair-engines", cmd_repair_engines),
        ("repair-reload-data", cmd_repair_reload),
    ):
        sub.add_parser(name).set_defaults(handler=handler)
    pause = sub.add_parser("pause")
    pause.add_argument("--now", action="store_true")
    pause.set_defaults(handler=cmd_pause)
    for name, handler in (("validate-data", cmd_validate), ("load-data", cmd_load)):
        p = sub.add_parser(name)
        p.add_argument("--file")
        p.set_defaults(handler=handler)
    one = sub.add_parser("run-one")
    one.add_argument("--engine", choices=ENGINES, required=True)
    one.add_argument("--profile", choices=list(PROFILES), required=True)
    one.add_argument("--query", choices=list(QUERIES) + list(PILOT_ONLY_QUERIES), required=True)
    one.add_argument("--concurrency", type=int, choices=CONCURRENCIES, required=True)
    one.add_argument("--variant", choices=list(VARIANTS), default="strict")
    one.set_defaults(handler=cmd_run_one)
    summ = sub.add_parser("summarize")
    summ.add_argument("--csv", help="Ruta al CSV a resumir (default: results/mediciones.csv; "
                                    "anexo: results/anexo_variantes.csv)")
    summ.set_defaults(handler=cmd_summarize)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
