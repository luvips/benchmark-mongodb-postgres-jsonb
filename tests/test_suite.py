import subprocess
import sys
from pathlib import Path

from benchmark_runner.classify import Signals, verdict
from benchmark_runner.config import CSV_HEADER, ROOT
from benchmark_runner.csvout import append_row, existing_keys
from benchmark_runner.stats import percentile, summarize
from benchmark_runner.workers import variant_options
from orchestrator.dataload import validate
from orchestrator.state import Journal, build_plan, derive


def test_variant_options_applied_to_both_q1_and_q2():
    assert variant_options("mongodb", "Q1", "strict") == {"allowDiskUse": False}
    assert variant_options("mongodb", "Q1", "relaxed") == {"allowDiskUse": True}
    assert variant_options("postgresql", "Q1", "strict") == {"work_mem": "4MB"}
    assert variant_options("postgresql", "Q1", "relaxed") == {"work_mem": "64MB"}
    assert variant_options("postgresql", "Q2", "strict") == {"work_mem": "4MB"}
    assert variant_options("postgresql", "Q2", "relaxed") == {"work_mem": "64MB"}


def test_classify_verdict_resilience():
    # OOM domina sobre conn_lost y devuelve fila válida con status="oom"
    s_oom = Signals(completed=0, censored=1, memory_errors=0, conn_lost=50,
                    other_errors=0, oom_kills=2, spill=True, telemetry_ok=True)
    v_oom = verdict(s_oom)
    assert not v_oom.fault and v_oom.status == "oom"

    # Error de memoria del motor (ej. código 292 en Mongo o 53200 en PG) es resultado válido
    s_mem = Signals(completed=10, censored=0, memory_errors=5, conn_lost=0,
                    other_errors=0, oom_kills=0, spill=False, telemetry_ok=True)
    v_mem = verdict(s_mem)
    assert not v_mem.fault and v_mem.status == "error"

    # Si hubo consultas completadas y algún conn_lost transitorio bajo contención, no aborta la celda
    s_trans = Signals(completed=120, censored=2, memory_errors=0, conn_lost=1,
                      other_errors=0, oom_kills=0, spill=True, telemetry_ok=True)
    v_trans = verdict(s_trans)
    assert not v_trans.fault and v_trans.status == "spill"


def test_plan_includes_36_base_and_q1_q2_relaxed():
    plan = build_plan(seed=42, repetitions=3)
    strict = [r for r in plan if r["variant"] == "strict"]
    relaxed = [r for r in plan if r["variant"] == "relaxed"]
    assert len(strict) == 36 * 3  # 108 corridas base
    # 2 motores x perfil C x (Q1, Q2) x c=(1, 10, 50) x 3 reps = 36 corridas de contraste M2
    assert len(relaxed) == 2 * 1 * 2 * 3 * 3
    assert {r["profile"] for r in relaxed} == {"C"}
    assert {r["concurrency"] for r in relaxed} == {1, 10, 50}
    assert len({r["id"] for r in plan}) == len(plan)


def test_relaxed_query_id_does_not_collide_on_concat():
    from benchmark_runner.config import query_label
    assert query_label("Q1", "strict") == "Q1"
    assert query_label("Q2", "relaxed") == "Q2_relaxed"


def test_sampler_engine_memory_is_per_sample_anon_plus_shmem(tmp_path: Path):
    from benchmark_runner.telemetry import Sampler

    def snap(anon, shmem, file):
        return {"anon": anon, "shmem": shmem, "file": file, "current": anon + shmem + file,
                "peak": 2000, "usage_usec": 0, "throttled_usec": 0, "nr_throttled": 0,
                "oom_kill": 0, "wbytes": 0}

    class FakeCgroup:
        snaps = iter([snap(100, 10, 900), snap(50, 80, 100)])

        def snapshot(self):
            return next(self.snaps)

    ticks = iter([0.0, 1.0])
    s = Sampler(FakeCgroup(), tmp_path / "series.csv", clock=lambda: next(ticks))
    s.mark("start")
    s.halt.wait = lambda timeout: s.window_samples >= 2  # dos iteraciones sin dormir
    s.run()
    assert s.window_samples == 2
    assert s.window_max_engine == 130   # máx por muestra (110, 130), no 100 + 80
    assert s.window_sum_engine == 240   # integral (110 + 130) bytes·s en las 2 muestras de 1 s
    assert s.window_max_file == 900     # page cache separado de la huella del motor


def _run_sampler(tmp_path, engine_values, ticks, start_at=0, end_after=None):
    """Ejecuta Sampler sin dormir sobre una serie sintética de (anon, shmem) y tiempos de reloj."""
    from benchmark_runner.telemetry import Sampler

    snaps = iter([
        {"anon": a, "shmem": sh, "file": 0, "current": a + sh, "peak": a + sh, "usage_usec": 0,
         "throttled_usec": 0, "nr_throttled": 0, "oom_kill": 0, "wbytes": 0}
        for a, sh in engine_values
    ])

    class FakeCgroup:
        def snapshot(self):
            return next(snaps)

    clock = iter(ticks)
    s = Sampler(FakeCgroup(), tmp_path / "series.csv", clock=lambda: next(clock))
    calls = {"n": 0}

    def fake_wait(timeout):
        if calls["n"] == start_at:
            s.mark("start")
        if end_after is not None and calls["n"] == end_after:
            s.mark("end")
        calls["n"] += 1
        return calls["n"] > len(engine_values)

    s.halt.wait = fake_wait
    s.run()
    return s


def test_memory_integral_matches_constant_level(tmp_path: Path):
    # 300 muestras a 1 s exactos con anon + shmem = 512 MiB constantes -> 512 MiB x 300 s
    mib = 1048576
    s = _run_sampler(tmp_path, [(384 * mib, 128 * mib)] * 300, [float(i) for i in range(300)])
    assert s.window_samples == 300
    assert s.window_sum_engine / mib == 512 * 300
    assert s.window_seconds == 300


def test_memory_integral_uses_measured_dt_and_excludes_warmup(tmp_path: Path):
    # Warmup (2 muestras de 999 bytes) excluido; en la ventana el Δt real varía (1.0, 2.5 por un
    # segundo sin lectura, 1.0): integral = 100*1 + 200*2.5 + 300*1 = 900 bytes·s, Σdt = 4.5 s.
    values = [(999, 0), (999, 0), (100, 0), (200, 0), (300, 0)]
    ticks = [0.0, 1.0, 2.0, 4.5, 5.5]
    s = _run_sampler(tmp_path, values, ticks, start_at=2)
    assert s.window_samples == 3
    assert s.window_sum_engine == 900
    assert s.window_seconds == 4.5
    assert s.window_max_engine == 300


def test_memory_integral_stops_at_window_end(tmp_path: Path):
    # La muestra que cierra la ventana se incluye; las de cooldown no.
    s = _run_sampler(tmp_path, [(10, 0), (20, 0), (30, 0), (1000, 0)], [0.0, 1.0, 2.0, 3.0], end_after=1)
    assert s.window_samples == 2
    assert s.window_sum_engine == 10 * 1.0 + 20 * 1.0


def test_m3_metrics_normalization():
    from benchmark_runner.runner import m3_metrics

    mib = 1048576
    # 120 consultas x 500 000 docs = 60 000 miles de docs; 600 CPU·s; integral 512 MiB x 300 s
    m3 = m3_metrics(120, 500_000, 600.0, 640 * mib, 512 * mib * 300)
    assert m3["kdocs_processed"] == 60_000
    assert m3["cpu_seconds_per_1k_docs"] == 0.01
    assert m3["memory_peak_mb"] == 640                       # nivel absoluto, sin normalizar
    assert m3["memory_mb_s_per_1k_docs"] == 512 * 300 / 60_000  # 2.56 MB·s por cada 1 000 docs
    # Duplicar las consultas completadas con la misma memoria integrada reduce Mem_1k a la mitad
    assert m3_metrics(240, 500_000, 600.0, 640 * mib, 512 * mib * 300)["memory_mb_s_per_1k_docs"] == 1.28
    # Sin consultas completadas (p. ej. OOM) no hay normalización posible
    none = m3_metrics(0, 500_000, 600.0, 640 * mib, 512 * mib * 300)
    assert none["cpu_seconds_per_1k_docs"] is None and none["memory_mb_s_per_1k_docs"] is None
    assert none["memory_peak_mb"] == 640


def test_csv_append_and_idempotency(tmp_path: Path):
    csv_path = tmp_path / "mediciones.csv"
    row = {
        "timestamp_epoch": 1793418513,
        "engine": "mongodb",
        "profile": "PROFILE_A_BASELINE",
        "query_id": "Q1",
        "concurrency": 1,
        "run_number": 1,
        "throughput_qps": 0.777,
        "latency_p50_ms": 1286.462,
        "latency_p95_ms": 1303.068,
        "latency_p99_ms": 1342.679,
        "cpu_throttled_seconds": 0.0,
        "max_rss_mb": 727.547,
        "disk_spill": False,
        "status": "ok",
    }
    assert append_row(csv_path, row) is True
    assert append_row(csv_path, row) is False
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].split(",") == CSV_HEADER
    assert len(existing_keys(csv_path)) == 1


def test_stats_percentile_and_summary():
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert percentile(vals, 50) == 30.0
    s = summarize(vals, window_s=10.0)
    assert s.completed == 5
    assert s.throughput_qps == 0.5


def test_generator_and_dataload_validation(tmp_path: Path):
    res = subprocess.run(
        [sys.executable, str(ROOT / "synthetic_data_generator.py"), "--n", "200", "--out-dir", str(tmp_path)],
        capture_output=True, text=True, check=True,
    )
    ndjson = tmp_path / "orders.ndjson"
    manifest = tmp_path / "dataset_manifest.json"
    assert ndjson.exists() and manifest.exists()
    report = validate(ndjson)
    assert report["count"] == 200
    assert report["errors"] == []
