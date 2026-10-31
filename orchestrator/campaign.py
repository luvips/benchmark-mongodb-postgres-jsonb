"""Bucle principal de la campaña experimental."""
import json
import shutil
import subprocess
import time
import traceback

from benchmark_runner import engine_stats
from benchmark_runner.config import (CONTAINERS, DATA_PATH, ENGINES, PROFILES, QUERIES,
                                     REHEARSAL, RESULTS_DIR, STATE_DIR)
from benchmark_runner.csvout import append_row
from benchmark_runner.runner import Aborted, execute
from benchmark_runner.telemetry import Cgroup, container_id, prom_query, prom_selector
from benchmark_runner.workers import SESSIONS

from . import explain, host, incident, manifest
from .host import Fault
from .state import Journal, derive

MIN_DISK_GIB = 5
CANARY_TELEMETRY_S = 30
MAIN_CSV = "mediciones.csv"
ANNEX_CSV = "anexo_variantes.csv"


def notify(text):
    try:
        subprocess.run(["notify-send", "Benchmark BD", text], capture_output=True, timeout=5)
    except Exception:
        pass


class Campaign:
    def __init__(self):
        self.journal = Journal(STATE_DIR / "journal.jsonl")
        self.plan = json.loads((STATE_DIR / "plan.json").read_text(encoding="utf-8"))
        self.control = STATE_DIR / "control"
        self.dataset = json.loads((STATE_DIR / "dataset.json").read_text(encoding="utf-8"))

    def derived(self):
        return derive(self.plan, self.journal.read())

    def control_value(self):
        return self.control.read_text(encoding="utf-8").strip() if self.control.exists() else ""

    def should_abort(self):
        return self.control_value() == "pause_now"

    def csv_path(self, run):
        return RESULTS_DIR / (ANNEX_CSV if run["variant"] != "strict" else MAIN_CSV)

    def raw_dir(self, run):
        return RESULTS_DIR / "raw" / run["id"]

    def discard(self, run_id, reason):
        raw = RESULTS_DIR / "raw" / run_id
        if raw.exists():
            target = RESULTS_DIR / "discarded" / f"{run_id}-{int(time.time())}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(raw, target)
        self.journal.append("run_discarded", run=run_id, reason=reason)

    def release_host(self):
        for step in (host.stop_engines, host.stop_telemetry, host.energy_restore):
            try:
                step()
            except Fault as fault:
                self.journal.append("cleanup_warning", step=step.__name__, reason=fault.reason)

    def pause(self):
        self.release_host()
        self.control.unlink(missing_ok=True)
        self.journal.append("paused")

    def halt(self, reason):
        self.release_host()
        self.journal.append("halted", reason=reason)
        notify(f"Campaña detenida: {reason}")

    def perform(self, run, suspects):
        if host.disk_free_gib() < MIN_DISK_GIB:
            raise Fault("disk_low", f"{host.disk_free_gib():.1f} GiB libres")
        self.journal.append("run_started", run=run["id"])
        # Recreación limpia del contenedor para vaciar shared_buffers y WiredTiger cache + drop_caches del kernel
        host.stop_engines()
        host.drop_caches()
        host.start_engine(run["engine"], run["profile"])
        host.wait_ready(run["engine"])
        rows = engine_stats.row_count(run["engine"])
        if abs(rows - self.dataset["count"]) > 0.01 * self.dataset["count"]:
            raise Fault("data_mismatch", f"{rows} filas, se esperaban {self.dataset['count']}")
        outcome = execute(run, self.raw_dir(run), self.should_abort)
        if outcome.fault:
            meta = self.raw_dir(run) / "meta.json"
            raise Fault(outcome.reason, meta.read_text(encoding="utf-8") if meta.exists() else "")
        if outcome.noisy and run["id"] not in suspects:
            target = RESULTS_DIR / "discarded" / f"{run['id']}-suspect-{int(time.time())}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(self.raw_dir(run), target)
            self.journal.append("run_suspect", run=run["id"])
            return
        append_row(self.csv_path(run), outcome.row)
        self.journal.append("run_finished", run=run["id"], status=outcome.row["status"])

    def canary(self, run):
        host.start_telemetry()
        host.stop_engines()
        host.start_engine(run["engine"], run["profile"])
        host.wait_ready(run["engine"])
        cid = container_id(CONTAINERS[run["engine"]])
        if not cid or not Cgroup(cid).exists():
            raise Fault("canary_cgroup_not_visible", run["engine"])
        session = SESSIONS[run["engine"]]("Q2", "relaxed")
        session.connect()
        session.close()
        name = CONTAINERS[run["engine"]]
        sel = prom_selector(cid)
        deadline = time.monotonic() + CANARY_TELEMETRY_S
        while time.monotonic() < deadline:
            try:
                if prom_query(f"container_memory_rss{sel}", time.time()) is not None:
                    return
            except OSError:
                pass
            time.sleep(2)
        raise Fault("canary_no_telemetry", name)

    def recover(self, run, fault):
        d = self.derived()
        if d.incidents >= incident.MAX_INCIDENTS:
            self.halt("incident_budget_exhausted")
            return False
        incident_id, directory = incident.open_incident(run, fault)
        self.journal.append("incident_opened", id=incident_id, run=run["id"], reason=fault.reason)
        notify(f"Incidencia {fault.reason}")
        csvs = [RESULTS_DIR / MAIN_CSV, RESULTS_DIR / ANNEX_CSV]
        prints = [manifest.fingerprint(p) for p in csvs]
        for attempt in range(1, incident.MAX_ATTEMPTS + 1):
            result = incident.spawn_agent(incident_id, directory, attempt)
            self.journal.append("incident_attempt", id=incident_id, attempt=attempt, **result)
            drift = manifest.changed(STATE_DIR / "manifest.json", DATA_PATH)
            intact = all(manifest.prefix_intact(p, f) for p, f in zip(csvs, prints))
            if drift or not intact:
                self.journal.append("manifest_violation", id=incident_id, drift=drift, csv_intact=intact)
                self.halt("manifest_violation")
                return False
            try:
                self.canary(run)
            except Exception as exc:
                self.journal.append("canary_failed", id=incident_id, detail=str(exc)[-500:])
                continue
            self.journal.append("incident_resolved", id=incident_id, attempt=attempt)
            if not REHEARSAL:
                incident.commit_fix(incident_id)
            return True
        self.halt("incident_unresolved")
        return False

    def attempt(self, run, suspects):
        fault = None
        for _ in range(2):
            try:
                self.perform(run, suspects)
                return True
            except Aborted:
                self.discard(run["id"], "pause_now")
                self.pause()
                return False
            except Fault as exc:
                fault = exc
            except Exception:
                fault = Fault("runner_exception", traceback.format_exc())
            self.discard(run["id"], fault.reason)
            self.journal.append("run_retry", run=run["id"], reason=fault.reason)
        return self.recover(run, fault)

    def explain_pass(self):
        out = RESULTS_DIR / "explain"
        out.mkdir(parents=True, exist_ok=True)
        for engine in ENGINES:
            for profile in PROFILES:
                for query in QUERIES:
                    for variant in ("strict", "relaxed"):
                        suffix = "" if variant == "strict" else f"-{variant}"
                        path = out / f"{engine}-{profile}-{query}{suffix}.txt"
                        if path.exists():
                            continue
                        host.stop_engines()
                        host.drop_caches()
                        host.start_engine(engine, profile)
                        host.wait_ready(engine)
                        path.write_text(explain.explain(engine, query, variant), encoding="utf-8")

    def finish(self):
        try:
            self.explain_pass()
        except Exception as exc:
            self.journal.append("explain_failed", detail=str(exc)[-500:])
        self.release_host()
        self.journal.append("done")
        notify("Campaña terminada")

    def main(self):
        d = self.derived()
        self.journal.append("resumed" if d.state != "new" else "campaign_started")
        if d.interrupted:
            self.discard(d.interrupted, "interrupted")
        try:
            host.start_telemetry()
            host.energy_performance()
        except Fault as fault:
            self.halt(fault.reason)
            return
        while True:
            if self.control_value():
                self.pause()
                return
            d = self.derived()
            if not d.pending:
                self.finish()
                return
            if not self.attempt(d.pending[0], d.suspects):
                return


if __name__ == "__main__":
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        Campaign().main()
    except Exception:
        Journal(STATE_DIR / "journal.jsonl").append(
            "halted", reason="orchestrator_crash", detail=traceback.format_exc()[-2000:]
        )
        notify("Campaña detenida: orchestrator_crash")
        raise
