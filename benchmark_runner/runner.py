"""Ejecuta una celda individual sobre un motor levantado y produce la fila CSV + meta.json completo."""
import json
import time
from dataclasses import asdict, dataclass

from . import engine_stats
from .classify import Signals, verdict
from .config import (CONTAINERS, COOLDOWN_S, DOCS_PER_QUERY, MEASURE_S, MIN_SAMPLE_RATIO,
                     NOISE_BUSY_PCT, PROFILE_SPECS, PROFILES, WARMUP_S, WORKER_JOIN_GRACE_S,
                     query_label)
from .stats import summarize
from .telemetry import (METRIC_SOURCE, Cgroup, Sampler, container_host_config, container_id,
                        container_state, prom_metrics, sources_agree)
from .workers import CTX, variant_options, worker


class Aborted(Exception):
    pass


class TelemetryUnavailable(RuntimeError):
    """El cgroup del contenedor no es visible desde el runner (p. ej. Docker Desktop en WSL2)."""


@dataclass
class Outcome:
    row: dict
    fault: bool
    reason: str
    noisy: bool


def _wait(until, should_abort):
    while (remaining := until - time.monotonic()) > 0:
        if should_abort():
            raise Aborted()
        time.sleep(min(0.5, remaining))


def _read_samples(raw_dir):
    samples = []
    with (raw_dir / "latencies.jsonl").open("w", encoding="utf-8") as merged:
        for path in sorted(raw_dir.glob("client-*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                merged.write(line + "\n")
            path.unlink()
    return samples


def m3_metrics(completed, docs_per_query, cpu_seconds, engine_max_bytes, engine_byte_seconds):
    """Métricas M3 por corrida.

    - CPU_1k: CPU·s / (miles de documentos procesados).
    - Mem_peak: nivel máximo de anon + shmem en la ventana (MB). Magnitud intensiva: no se normaliza.
    - Mem_1k: integral de anon + shmem en la ventana (MB·s) / (miles de documentos procesados).
    Con 0 consultas completadas las métricas normalizadas son None (no hay documentos procesados).
    """
    kdocs = completed * docs_per_query / 1000.0
    mb = 1048576.0
    return {
        "kdocs_processed": kdocs,
        "cpu_seconds_per_1k_docs": cpu_seconds / kdocs if (cpu_seconds is not None and kdocs > 0) else None,
        "memory_peak_mb": engine_max_bytes / mb if engine_max_bytes else None,
        "memory_mb_s_per_1k_docs": (engine_byte_seconds / mb) / kdocs if (engine_byte_seconds and kdocs > 0) else None,
    }


def _count(samples, kind):
    return sum(1 for s in samples if s["k"] == kind)


def execute(run, raw_dir, should_abort):
    raw_dir.mkdir(parents=True, exist_ok=True)
    engine, name = run["engine"], CONTAINERS[run["engine"]]
    cid = container_id(name)
    cgroup = Cgroup(cid)
    if not cid or not cgroup.exists():
        # Sin el cgroup se pierden io.stat (3.ª señal de spill), picos de memoria y oom_kill:
        # se aborta en lugar de producir una fila con telemetría degradada en silencio.
        raise TelemetryUnavailable(f"cgroup_not_visible: {cgroup.path}")
    start_epoch = time.time()

    # Capturamos versión y límites Docker reales ANTES del warmup (Diapositiva 5)
    version_str = engine_stats.engine_version(engine)
    docker_limits = container_host_config(name)
    session_opts = variant_options(engine, run["query"], run["variant"])
    before = engine_stats.counters(engine)
    sampler = Sampler(cgroup, raw_dir / "series.csv")

    n = run["concurrency"]
    barrier = CTX.Barrier(n + 1)
    t_measure, t_end = CTX.Value("d", 0.0), CTX.Value("d", 0.0)
    clients = [
        CTX.Process(
            target=worker,
            daemon=True,
            args=(run, raw_dir / f"client-{i:02d}.jsonl", barrier, t_measure, t_end),
        )
        for i in range(n)
    ]
    for c in clients:
        c.start()
    barrier.wait(timeout=60)
    t0 = time.monotonic() + WARMUP_S
    t_measure.value, t_end.value = t0, t0 + MEASURE_S
    sampler.start()
    barrier.wait(timeout=60)

    try:
        # 1. Warmup (60 s)
        _wait(t0, should_abort)
        # 2. Inicio de ventana de medición (300 s): tomamos contadores justo al abrir la ventana
        epoch0 = time.time()
        try:
            before_window = engine_stats.counters(engine)
        except Exception:
            before_window = before
        sampler.mark("start")
        _wait(t0 + MEASURE_S, should_abort)
        epoch1 = time.time()
        sampler.mark("end")

        deadline = time.monotonic() + WORKER_JOIN_GRACE_S
        hung = 0
        for c in clients:
            c.join(max(0, deadline - time.monotonic()))
            if c.is_alive():
                hung += 1
                c.terminate()
        # 3. Cooldown (60 s)
        _wait(t0 + MEASURE_S + COOLDOWN_S, should_abort)
    except Aborted:
        for c in clients:
            c.terminate()
        sampler.stop()
        raise
    sampler.stop()

    samples = _read_samples(raw_dir)
    in_window = [s for s in samples if 0 <= s["e"] <= MEASURE_S]
    latencies = [(s["e"] - s["s"]) * 1000 for s in in_window if s["k"] == "ok"]
    summary = summarize(latencies, MEASURE_S)

    state = container_state(name)
    running = state.get("Running", False)
    last = sampler.last
    oom_kills = int(last.get("oom_kill", 0)) + (1 if state.get("OOMKilled") and not last.get("oom_kill") else 0)
    try:
        after = engine_stats.counters(engine) if running else {}
    except Exception:
        after = {}

    wbytes_window = sampler.written_bytes()
    spill, spill_lines = engine_stats.spill_evidence(engine, before_window, after, epoch0, wbytes_window)

    prom_cpu_s = None
    prom_ws_bytes = None
    if METRIC_SOURCE == "prometheus":
        try:
            throttled, prom_cpu_s, rss_bytes, prom_ws_bytes, n_samples = prom_metrics(cid, epoch0, epoch1)
        except OSError:
            throttled, prom_cpu_s, rss_bytes, prom_ws_bytes, n_samples = None, None, None, None, 0
    else:
        throttled = sampler.throttled_seconds()
        rss_bytes = sampler.window_max_anon or last.get("anon")
        n_samples = sampler.window_samples

    # Fallback seguro: si Prometheus no devolvió RSS (ej. contenedor murió por OOM), usamos el máximo anon de la ventana
    if rss_bytes is None and sampler.window_max_anon > 0:
        rss_bytes = float(sampler.window_max_anon)

    telemetry_ok = (
        throttled is not None
        and n_samples >= MEASURE_S * MIN_SAMPLE_RATIO
        and sampler.window_samples >= MEASURE_S * MIN_SAMPLE_RATIO
        and sources_agree(throttled, sampler.throttled_seconds())
    )

    signals = Signals(
        completed=summary.completed,
        censored=_count(samples, "cancelled") + hung,
        memory_errors=_count(samples, "memory"),
        conn_lost=_count(samples, "conn_lost"),
        other_errors=_count(samples, "other"),
        oom_kills=oom_kills,
        spill=spill,
        telemetry_ok=telemetry_ok,
    )
    result = verdict(signals)
    noisy = sampler.idle_sibling_busy_pct() > NOISE_BUSY_PCT

    # Cálculo de M3 (Huella de CPU y Memoria por cada 1 000 documentos procesados)
    cpu_used_s = sampler.cpu_seconds() if sampler.cpu_seconds() is not None else prom_cpu_s
    docs_per_q = DOCS_PER_QUERY.get(run["query"], 500_000)
    # (a) Nivel máximo de memoria del motor en la ventana: máx. por muestra de anon + shmem (MB absolutos,
    #     magnitud intensiva que no acumula con el número de consultas).
    # (b) Huella acumulada de memoria por cada 1 000 documentos: integral de (anon + shmem) en la ventana
    #     (MB·s, magnitud extensiva análoga a CPU·s) dividida entre los miles de documentos procesados.
    engine_mem_bytes = sampler.window_max_engine
    m3 = m3_metrics(summary.completed, docs_per_q, cpu_used_s, engine_mem_bytes, sampler.window_sum_engine)
    kdocs_processed = m3["kdocs_processed"]
    cpu_sec_per_1k_docs = m3["cpu_seconds_per_1k_docs"]
    peak_mb = m3["memory_peak_mb"]
    mem_mb_s_per_1k_docs = m3["memory_mb_s_per_1k_docs"]
    peak_bytes = max(sampler.window_max_peak, last.get("peak", 0))

    (raw_dir / "meta.json").write_text(
        json.dumps({
            "run": run,
            "engine_version": version_str,
            "profile_spec": PROFILE_SPECS.get(run["profile"], {}),
            "limits": docker_limits,
            "variant": run["variant"],
            "session_options": session_opts,
            "n_completed": summary.completed,
            "n_censored": signals.censored,
            "signals": asdict(signals),
            "verdict": asdict(result),
            "noisy": noisy,
            "idle_sibling_busy_pct": sampler.idle_sibling_busy_pct(),
            "cpu_usage_seconds": cpu_used_s,
            "prometheus_cpu_usage_seconds": prom_cpu_s,
            "cgroup_throttled_seconds": sampler.throttled_seconds(),
            "nr_throttled_final": last.get("nr_throttled"),
            "m3_docs_per_query": docs_per_q,
            "m3_total_kdocs_processed": kdocs_processed,
            "m3_cpu_seconds_per_1k_docs": cpu_sec_per_1k_docs,
            "m3_memory_peak_mb": peak_mb,
            "m3_memory_mb_s_per_1k_docs": mem_mb_s_per_1k_docs,
            "engine_mem_max_bytes": engine_mem_bytes,
            "engine_mem_byte_seconds": sampler.window_sum_engine,
            "engine_mem_integral_seconds": sampler.window_seconds,
            "memory_peak_bytes": peak_bytes,
            "prometheus_working_set_max_bytes": prom_ws_bytes,
            "anon_max_bytes": sampler.window_max_anon,
            "shmem_max_bytes": sampler.window_max_shmem,
            "file_max_bytes": sampler.window_max_file,
            "current_max_bytes": sampler.window_max_current,
            "cgroup_window_samples": sampler.window_samples,
            "anon_final": last.get("anon"),
            "shmem_final": last.get("shmem"),
            "file_final": last.get("file"),
            "before_window": before_window,
            "after": after,
            "written_bytes_window": wbytes_window,
            "hung_clients": hung,
            "prometheus_samples": n_samples,
            "metric_source": METRIC_SOURCE,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if oom_kills:
        (raw_dir / "oom_evidence.log").write_text(
            engine_stats.oom_evidence(engine, start_epoch, state), encoding="utf-8"
        )
    if spill_lines:
        (raw_dir / "spill_evidence.log").write_text(
            "\n".join(spill_lines) + "\n", encoding="utf-8"
        )

    row = {
        "timestamp_epoch": int(epoch0),
        "engine": engine,
        "profile": PROFILES[run["profile"]],
        "query_id": query_label(run["query"], run["variant"]),
        "concurrency": n,
        "run_number": run["rep"],
        "throughput_qps": summary.throughput_qps,
        "latency_p50_ms": summary.p50_ms,
        "latency_p95_ms": summary.p95_ms,
        "latency_p99_ms": summary.p99_ms,
        "cpu_throttled_seconds": throttled,
        "max_rss_mb": None if rss_bytes is None else rss_bytes / 1048576,
        "disk_spill": spill,
        "status": result.status,
    }
    return Outcome(row, result.fault, result.reason, noisy)
