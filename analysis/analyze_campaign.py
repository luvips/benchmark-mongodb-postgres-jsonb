#!/usr/bin/env python3
"""
analysis/analyze_campaign.py
============================
Script de agregación estadística, cálculo de métricas M1/M2/M3 y generación de
gráficas y tablas Markdown para el Reporte Técnico (Entregable 6).

Lee únicamente archivos reales generados por el runner:
  - results/mediciones.csv          (matriz base: 36 celdas x 3 rep = 108 corridas)
  - results/anexo_variantes.csv     (anexo de contraste: 24 celdas x 3 rep = 72 corridas)
  - results/raw/<run_id>/meta.json  (telemetría extendida, contadores y métricas M3)
  - results/raw/<run_id>/series.csv (serie temporal de 1 s de cgroups v2)

Modo de prueba durante la campaña (--include-pilot):
  Si la campaña aún está en curso o recién inicia, permite incorporar las corridas
  de results/pilot/ para validar el pipeline de gráficas y tablas con datos reales.

Salidas en --out-dir (default: results/analysis):
  - summary_metrics.json            (todas las celdas agregadas con mediana y [min, max])
  - tables_summary.md               (tablas Markdown listas para las secciones 3, 4 y 6)
  - figures/fig1_p99_vs_concurrency.png
  - figures/fig2_throttling_vs_qps.png
  - figures/fig3_memory_timeseries.png
  - figures/fig4_m3_efficiency.png
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

from benchmark_runner.config import MIN_P99_SAMPLES, PROFILES, ROOT

PROFILE_TO_LETTER = {v: k for k, v in PROFILES.items()}
LETTER_TO_PROFILE = dict(PROFILES)
PROFILE_ORDER = ["PROFILE_A_BASELINE", "PROFILE_B_MEDIUM", "PROFILE_C_SEVERE"]
ENGINE_COLORS = {"mongodb": "#13aa52", "postgresql": "#336791"}
ENGINE_LABELS = {"mongodb": "MongoDB 9.0.4 (WiredTiger)", "postgresql": "PostgreSQL 18.6 (JSONB)"}


def _to_float(val):
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _med_range(values: list[float | None]) -> tuple[float | None, float | None, float | None]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None, None, None
    return (
        round(statistics.median(clean), 4),
        round(min(clean), 4),
        round(max(clean), 4),
    )


def _fmt_med_range(med: float | None, lo: float | None, hi: float | None, decimals: int = 2) -> str:
    if med is None:
        return "—"
    if lo is None or hi is None or abs(hi - lo) < 1e-9:
        return f"{med:.{decimals}f}"
    return f"{med:.{decimals}f} [{lo:.{decimals}f}, {hi:.{decimals}f}]"


def _resolve_meta_and_series(row: dict, results_dir: Path) -> tuple[dict, Path | None]:
    """Localiza meta.json y series.csv en results/raw/ o en results/pilot/."""
    if "_meta_path" in row and Path(row["_meta_path"]).exists():
        meta_path = Path(row["_meta_path"])
        series_path = meta_path.parent / "series.csv"
        return json.loads(meta_path.read_text(encoding="utf-8")), (series_path if series_path.exists() else None)

    letter = PROFILE_TO_LETTER.get(row["profile"], row["profile"])
    qid = row["query_id"]
    base_q = qid.split("_", 1)[0]
    suffix = "" if qid == base_q else "-" + qid.split("_", 1)[1]
    run_id = f"{row['engine']}-{letter}-{base_q}-c{row['concurrency']}-r{row['run_number']}{suffix}"
    raw_dir = results_dir / "raw" / run_id
    meta_path = raw_dir / "meta.json"
    if meta_path.exists():
        series_path = raw_dir / "series.csv"
        return json.loads(meta_path.read_text(encoding="utf-8")), (series_path if series_path.exists() else None)
    return {}, None


def load_rows(results_dir: Path, include_pilot: bool = False) -> list[dict]:
    rows: list[dict] = []
    for csv_name in ("mediciones.csv", "anexo_variantes.csv"):
        csv_path = results_dir / csv_name
        if csv_path.exists():
            with csv_path.open(encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    r["_source"] = csv_name
                    rows.append(r)

    if include_pilot:
        pilot_dir = results_dir / "pilot"
        if pilot_dir.exists():
            for p_csv in sorted(pilot_dir.glob("pilot-*.csv")):
                run_id = p_csv.stem.removeprefix("pilot-")
                meta_file = pilot_dir / run_id / "meta.json"
                with p_csv.open(encoding="utf-8", newline="") as f:
                    for r in csv.DictReader(f):
                        if r.get("throughput_qps") in (None, ""):
                            continue
                        if not r.get("status"):
                            r["status"] = "spill" if str(r.get("disk_spill")).lower() == "true" else "ok"
                        r["_source"] = "pilot"
                        if meta_file.exists():
                            r["_meta_path"] = str(meta_file)
                        rows.append(r)
    return rows


def aggregate_cells(rows: list[dict], results_dir: Path) -> list[dict]:
    grouped: dict[tuple[str, str, str, int], list[dict]] = defaultdict(list)
    for r in rows:
        key = (r["engine"], r["profile"], r["query_id"], int(r["concurrency"]))
        grouped[key].append(r)

    cells: list[dict] = []
    for (engine, profile, query_id, conc), cell_rows in sorted(
        grouped.items(),
        key=lambda item: (
            item[0][2],
            PROFILE_ORDER.index(item[0][1]) if item[0][1] in PROFILE_ORDER else 99,
            item[0][0],
            item[0][3],
        ),
    ):
        qps_m, qps_lo, qps_hi = _med_range([_to_float(r["throughput_qps"]) for r in cell_rows])
        p50_m, p50_lo, p50_hi = _med_range([_to_float(r["latency_p50_ms"]) for r in cell_rows])
        p95_m, p95_lo, p95_hi = _med_range([_to_float(r["latency_p95_ms"]) for r in cell_rows])
        p99_m, p99_lo, p99_hi = _med_range([_to_float(r["latency_p99_ms"]) for r in cell_rows])
        thr_m, thr_lo, thr_hi = _med_range([_to_float(r["cpu_throttled_seconds"]) for r in cell_rows])
        rss_m, rss_lo, rss_hi = _med_range([_to_float(r["max_rss_mb"]) for r in cell_rows])

        metas = []
        series_files = []
        for r in cell_rows:
            m, s_path = _resolve_meta_and_series(r, results_dir)
            if m:
                metas.append(m)
            if s_path:
                series_files.append(str(s_path))

        ns = [m.get("n_completed") for m in metas if m.get("n_completed") is not None]
        cpu_1k_m, cpu_1k_lo, cpu_1k_hi = _med_range([_to_float(m.get("m3_cpu_seconds_per_1k_docs")) for m in metas])
        mem_peak_m, mem_peak_lo, mem_peak_hi = _med_range([_to_float(m.get("m3_memory_peak_mb")) for m in metas])
        mem_1k_m, mem_1k_lo, mem_1k_hi = _med_range([_to_float(m.get("m3_memory_mb_s_per_1k_docs")) for m in metas])
        wbytes_m, _, _ = _med_range([_to_float(m.get("written_bytes_window")) for m in metas])

        spills = sum(1 for r in cell_rows if str(r.get("disk_spill")).lower() == "true")
        statuses = [r.get("status", "") for r in cell_rows]
        min_n = min(ns) if ns else None

        cells.append({
            "engine": engine,
            "profile": profile,
            "query_id": query_id,
            "concurrency": conc,
            "runs": len(cell_rows),
            "statuses": statuses,
            "spill_runs": spills,
            "min_n_completed": min_n,
            "p99_reliable": bool(ns) and (min_n is not None and min_n >= MIN_P99_SAMPLES),
            "qps": {"median": qps_m, "min": qps_lo, "max": qps_hi},
            "p50_ms": {"median": p50_m, "min": p50_lo, "max": p50_hi},
            "p95_ms": {"median": p95_m, "min": p95_lo, "max": p95_hi},
            "p99_ms": {"median": p99_m, "min": p99_lo, "max": p99_hi},
            "throttled_s": {"median": thr_m, "min": thr_lo, "max": thr_hi},
            "max_rss_mb": {"median": rss_m, "min": rss_lo, "max": rss_hi},
            "m3_cpu_s_per_1k_docs": {"median": cpu_1k_m, "min": cpu_1k_lo, "max": cpu_1k_hi},
            "m3_memory_peak_mb": {"median": mem_peak_m, "min": mem_peak_lo, "max": mem_peak_hi},
            "m3_memory_mb_s_per_1k_docs": {"median": mem_1k_m, "min": mem_1k_lo, "max": mem_1k_hi},
            "written_mb_window_median": round(wbytes_m / (1024 * 1024), 2) if wbytes_m is not None else None,
            "series_files": series_files,
        })
    return cells


def compute_m1_degradation(cells: list[dict]) -> list[dict]:
    """Calcula ΔP99 (%) y ΔQPS (%) de PROFILE_B y PROFILE_C respecto a PROFILE_A_BASELINE."""
    by_key = {(c["engine"], c["query_id"], c["concurrency"], c["profile"]): c for c in cells}
    m1_rows = []
    for query_id in ("Q1", "Q2"):
        for engine in ("mongodb", "postgresql"):
            for conc in (1, 10, 50):
                base = by_key.get((engine, query_id, conc, "PROFILE_A_BASELINE"))
                if not base:
                    continue
                qps_a = base["qps"]["median"]
                p99_a = base["p99_ms"]["median"]
                for prof in ("PROFILE_B_MEDIUM", "PROFILE_C_SEVERE"):
                    cur = by_key.get((engine, query_id, conc, prof))
                    if not cur:
                        continue
                    qps_x = cur["qps"]["median"]
                    p99_x = cur["p99_ms"]["median"]
                    d_qps = ((qps_x - qps_a) / qps_a * 100.0) if (qps_a and qps_x is not None) else None
                    d_p99 = ((p99_x - p99_a) / p99_a * 100.0) if (p99_a and p99_x is not None) else None
                    m1_rows.append({
                        "query_id": query_id,
                        "engine": engine,
                        "concurrency": conc,
                        "profile": prof,
                        "qps_baseline": qps_a,
                        "qps_profile": qps_x,
                        "delta_qps_pct": round(d_qps, 2) if d_qps is not None else None,
                        "p99_baseline_ms": p99_a,
                        "p99_profile_ms": p99_x,
                        "delta_p99_pct": round(d_p99, 2) if d_p99 is not None else None,
                        "throttled_s_median": cur["throttled_s"]["median"],
                        "statuses": cur["statuses"],
                    })
    return m1_rows


def generate_markdown_tables(cells: list[dict], m1_rows: list[dict]) -> str:
    lines = [
        "# Tablas Consolidadas de Resultados (Mediana y Rango `[min, max]`)",
        "",
        "## 1. Matriz Principal y Anexo (`Q1`, `Q2`, `Q1_relaxed`, `Q2_relaxed`)",
        "",
        "| Consulta | Perfil | Motor | `c` | Corridas | Estado(s) | QPS (med `[min, max]`) | P50 ms | P95 ms | P99 ms (med `[min, max]`) | $N_{\\min}$ | P99 Confiable | CFS Throttled (s) | Max RSS (MB) | Spill |",
        "|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    for c in cells:
        qps_str = _fmt_med_range(c["qps"]["median"], c["qps"]["min"], c["qps"]["max"], 3)
        p50_str = _fmt_med_range(c["p50_ms"]["median"], None, None, 1)
        p95_str = _fmt_med_range(c["p95_ms"]["median"], None, None, 1)
        p99_str = _fmt_med_range(c["p99_ms"]["median"], c["p99_ms"]["min"], c["p99_ms"]["max"], 1)
        thr_str = _fmt_med_range(c["throttled_s"]["median"], c["throttled_s"]["min"], c["throttled_s"]["max"], 2)
        rss_str = _fmt_med_range(c["max_rss_mb"]["median"], c["max_rss_mb"]["min"], c["max_rss_mb"]["max"], 1)
        stat_str = ",".join(sorted(set(c["statuses"])))
        n_str = str(c["min_n_completed"]) if c["min_n_completed"] is not None else "—"
        rel_str = "Sí" if c["p99_reliable"] else "No ($N<100$)"
        spill_str = f"{c['spill_runs']}/{c['runs']}"
        lines.append(
            f"| `{c['query_id']}` | `{c['profile']}` | `{c['engine']}` | {c['concurrency']} | {c['runs']} "
            f"| `{stat_str}` | {qps_str} | {p50_str} | {p95_str} | {p99_str} | {n_str} | {rel_str} "
            f"| {thr_str} | {rss_str} | {spill_str} |"
        )

    lines += [
        "",
        "## 2. Métrica M3: Huella de CPU y Memoria `(a) Pico + (b) Integral` por cada $1\\,000$ Documentos",
        "",
        "| Consulta | Perfil | Motor | `c` | $\\text{CPU}_{1\\text{k}}$ ($\\text{CPU}\\cdot\\text{s}/10^3\\text{ docs}$) | `(a)` $\\text{Mem}_{\\text{peak}}$ ($\\text{MB}$) | `(b)` $\\text{Mem}_{1\\text{k}}$ ($\\text{MB}\\cdot\\text{s}/10^3\\text{ docs}$) | Escrituras Bloque Ventana (MB) |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for c in cells:
        cpu1k = _fmt_med_range(
            c["m3_cpu_s_per_1k_docs"]["median"],
            c["m3_cpu_s_per_1k_docs"]["min"],
            c["m3_cpu_s_per_1k_docs"]["max"],
            5,
        )
        mpeak = _fmt_med_range(
            c["m3_memory_peak_mb"]["median"],
            c["m3_memory_peak_mb"]["min"],
            c["m3_memory_peak_mb"]["max"],
            1,
        )
        m1k = _fmt_med_range(
            c["m3_memory_mb_s_per_1k_docs"]["median"],
            c["m3_memory_mb_s_per_1k_docs"]["min"],
            c["m3_memory_mb_s_per_1k_docs"]["max"],
            3,
        )
        wb = f"{c['written_mb_window_median']:.1f}" if c["written_mb_window_median"] is not None else "—"
        lines.append(
            f"| `{c['query_id']}` | `{c['profile']}` | `{c['engine']}` | {c['concurrency']} "
            f"| {cpu1k} | {mpeak} | {m1k} | {wb} |"
        )

    if m1_rows:
        lines += [
            "",
            "## 3. Métrica M1: Degradación por Contención CFS (`B` y `C` vs `A`)",
            "",
            "| Consulta | Motor | `c` | Perfil Comparado | QPS Base (`A`) | QPS Perfil | $\\Delta\\text{QPS}$ (\\%) | P99 Base (`A`, ms) | P99 Perfil (ms) | $\\Delta\\text{P99}$ (\\%) | CFS Throttled (s) |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in m1_rows:
            dqps = f"{r['delta_qps_pct']:+.1f}%" if r["delta_qps_pct"] is not None else "—"
            dp99 = f"{r['delta_p99_pct']:+.1f}%" if r["delta_p99_pct"] is not None else "—"
            qps_b = f"{r['qps_baseline']:.3f}" if r["qps_baseline"] is not None else "—"
            qps_p = f"{r['qps_profile']:.3f}" if r["qps_profile"] is not None else "—"
            p99_b = f"{r['p99_baseline_ms']:.1f}" if r["p99_baseline_ms"] is not None else "—"
            p99_p = f"{r['p99_profile_ms']:.1f}" if r["p99_profile_ms"] is not None else "—"
            thr = f"{r['throttled_s_median']:.2f}" if r["throttled_s_median"] is not None else "—"
            lines.append(
                f"| `{r['query_id']}` | `{r['engine']}` | {r['concurrency']} | `{r['profile']}` "
                f"| {qps_b} | {qps_p} | {dqps} | {p99_b} | {p99_p} | {dp99} | {thr} |"
            )
    return "\n".join(lines) + "\n"


def plot_figures(cells: list[dict], fig_dir: Path) -> list[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    fig_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    # 1. Figura 1: Latencia P99 vs Concurrencia por Consulta y Perfil
    fig, axes = plt.subplots(2, 3, figsize=(15, 8.5), sharex=True)
    for row_idx, qid in enumerate(("Q1", "Q2")):
        for col_idx, prof in enumerate(PROFILE_ORDER):
            ax = axes[row_idx][col_idx]
            for engine in ("mongodb", "postgresql"):
                subset = [
                    c for c in cells
                    if c["query_id"] == qid and c["profile"] == prof and c["engine"] == engine
                    and c["p99_ms"]["median"] is not None
                ]
                if not subset:
                    continue
                xs = [c["concurrency"] for c in subset]
                ys = [c["p99_ms"]["median"] for c in subset]
                yerr_low = [max(0.0, c["p99_ms"]["median"] - (c["p99_ms"]["min"] or c["p99_ms"]["median"])) for c in subset]
                yerr_high = [max(0.0, (c["p99_ms"]["max"] or c["p99_ms"]["median"]) - c["p99_ms"]["median"]) for c in subset]
                ax.errorbar(
                    xs, ys, yerr=[yerr_low, yerr_high],
                    marker="o", linewidth=2, capsize=4,
                    color=ENGINE_COLORS[engine], label=ENGINE_LABELS[engine],
                )
            ax.set_title(f"{qid} — {prof.replace('PROFILE_', '')}", fontsize=10, fontweight="bold")
            ax.set_xticks([1, 10, 50])
            ax.set_yscale("log")
            ax.grid(True, which="both", linestyle="--", alpha=0.4)
            if col_idx == 0:
                ax.set_ylabel("Latencia P99 (ms, escala log)")
            if row_idx == 1:
                ax.set_xlabel("Concurrencia (clientes)")
    handles, labels = axes[0][0].get_legend_handles_labels()
    if not handles:
        for ax in axes.flat:
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                break
    if handles:
        fig.legend(handles, labels, loc="upper center", ncol=2, frameon=True, fontsize=10)
    fig.suptitle("M1/M2: Latencia P99 vs Concurrencia (Mediana y Rango [min, max])", fontsize=12, fontweight="bold", y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    f1 = fig_dir / "fig1_p99_vs_concurrency.png"
    fig.savefig(f1, dpi=180)
    plt.close(fig)
    generated.append(str(f1))

    # 2. Figura 2: Segundos de CPU Throttled (CFS) vs Throughput (QPS)
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    for idx, qid in enumerate(("Q1", "Q2")):
        ax = axes[idx]
        for engine in ("mongodb", "postgresql"):
            subset = [
                c for c in cells
                if c["query_id"] == qid and c["engine"] == engine
                and c["throttled_s"]["median"] is not None and c["qps"]["median"] is not None
            ]
            if not subset:
                continue
            xs = [c["throttled_s"]["median"] for c in subset]
            ys = [c["qps"]["median"] for c in subset]
            ax.scatter(xs, ys, s=70, color=ENGINE_COLORS[engine], label=ENGINE_LABELS[engine], alpha=0.85)
            for c in subset:
                short_p = PROFILE_TO_LETTER.get(c["profile"], c["profile"])
                ax.annotate(
                    f"{short_p}:c{c['concurrency']}",
                    (c["throttled_s"]["median"], c["qps"]["median"]),
                    textcoords="offset points", xytext=(5, 4), fontsize=8,
                )
        ax.set_title(f"{qid}: CFS Throttling vs Throughput (QPS)", fontweight="bold")
        ax.set_xlabel("Tiempo de CPU Throttled en ventana de 300 s (s)")
        ax.set_ylabel("Throughput Mediano (QPS)")
        ax.grid(True, linestyle="--", alpha=0.4)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=9)
    fig.tight_layout()
    f2 = fig_dir / "fig2_throttling_vs_qps.png"
    fig.savefig(f2, dpi=180)
    plt.close(fig)
    generated.append(str(f2))

    # 3. Figura 3: Curva temporal de memoria cgroups v2 (anon, shmem, file) en ventana de medición
    series_candidates = [c for c in cells if c["series_files"]]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2), sharey=True)
    for idx, engine in enumerate(("mongodb", "postgresql")):
        ax = axes[idx]
        chosen = next(
            (c for c in series_candidates if c["engine"] == engine and c["profile"] == "PROFILE_C_SEVERE" and c["query_id"] == "Q1"),
            next((c for c in series_candidates if c["engine"] == engine), None),
        )
        if chosen and chosen["series_files"]:
            s_path = Path(chosen["series_files"][0])
            ts_list, anon_mb, shmem_mb, file_mb = [], [], [], []
            with s_path.open(encoding="utf-8", newline="") as sf:
                reader = csv.DictReader(sf)
                t0 = None
                for srow in reader:
                    t = float(srow.get("t") or srow.get("ts"))
                    if t0 is None:
                        t0 = t
                    rel_t = t - t0
                    ts_list.append(rel_t)
                    anon_mb.append(float(srow["anon"]) / (1024 * 1024))
                    shmem_mb.append(float(srow["shmem"]) / (1024 * 1024))
                    file_mb.append(float(srow["file"]) / (1024 * 1024))
            if ts_list:
                eng_mb = [a + s for a, s in zip(anon_mb, shmem_mb)]
                ax.plot(ts_list, anon_mb, label="anon (RSS privado, MB)", color="#d95f02", linewidth=1.8)
                ax.plot(ts_list, shmem_mb, label="shmem (shared_buffers, MB)", color="#7570b3", linewidth=1.8)
                ax.plot(ts_list, eng_mb, label="anon + shmem (Motor M3, MB)", color="#1b9e77", linewidth=2.2)
                ax.plot(ts_list, file_mb, label="file (Page Cache SO, MB)", color="#666666", linestyle=":", linewidth=1.4)
                ax.axvline(60, color="#333333", linestyle="-.", linewidth=1.0, alpha=0.7, label="Inicio medición (60 s)")
                ax.axvline(360, color="#333333", linestyle="-.", linewidth=1.0, alpha=0.7, label="Fin medición (360 s)")
                if chosen["profile"] == "PROFILE_C_SEVERE":
                    ax.axhline(1024, color="red", linestyle="--", linewidth=1.2, label="Límite cgroup (1024 MB)")
                ax.set_title(
                    f"{ENGINE_LABELS[engine]}\n({chosen['query_id']}, {chosen['profile']}, c={chosen['concurrency']})",
                    fontsize=10, fontweight="bold",
                )
        ax.set_xlabel("Tiempo transcurrido desde inicio de warmup (s)")
        if idx == 0:
            ax.set_ylabel("Memoria cgroup v2 (MB)")
        ax.grid(True, linestyle="--", alpha=0.4)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7.5, loc="lower right")
    fig.suptitle("M2/M3: Desglose Temporal de Memoria en cgroups v2 (anon + shmem vs page cache)", fontsize=12, fontweight="bold", y=0.99)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    f3 = fig_dir / "fig3_memory_timeseries.png"
    fig.savefig(f3, dpi=180)
    plt.close(fig)
    generated.append(str(f3))

    # 4. Figura 4: Métrica M3 (CPU_1k, Mem_peak (a), Mem_1k (b))
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 5.0))
    metrics_spec = [
        ("m3_cpu_s_per_1k_docs", "M3 CPU: CPU·s / 1 000 docs", "CPU·s / 1k docs"),
        ("m3_memory_peak_mb", "M3 Memoria (a): Pico anon+shmem (MB)", "MB (nivel intensivo)"),
        ("m3_memory_mb_s_per_1k_docs", "M3 Memoria (b): Integral MB·s / 1 000 docs", "MB·s / 1k docs (extensiva)"),
    ]
    for idx, (m_key, title, ylabel) in enumerate(metrics_spec):
        ax = axes[idx]
        labels_x = []
        mongo_vals = []
        pg_vals = []
        for qid in ("Q1", "Q2"):
            for prof in PROFILE_ORDER:
                for conc in (1, 10, 50):
                    m_cell = next(
                        (c for c in cells if c["engine"] == "mongodb" and c["query_id"] == qid and c["profile"] == prof and c["concurrency"] == conc),
                        None,
                    )
                    p_cell = next(
                        (c for c in cells if c["engine"] == "postgresql" and c["query_id"] == qid and c["profile"] == prof and c["concurrency"] == conc),
                        None,
                    )
                    m_v = m_cell[m_key]["median"] if m_cell else None
                    p_v = p_cell[m_key]["median"] if p_cell else None
                    if m_v is None and p_v is None:
                        continue
                    short_p = PROFILE_TO_LETTER.get(prof, prof)
                    labels_x.append(f"{qid}\n{short_p}:c{conc}")
                    mongo_vals.append(m_v or 0.0)
                    pg_vals.append(p_v or 0.0)
        if labels_x:
            x_pos = list(range(len(labels_x)))
            width = 0.38
            ax.bar([x - width / 2 for x in x_pos], mongo_vals, width=width, color=ENGINE_COLORS["mongodb"], label="MongoDB 9.0.4")
            ax.bar([x + width / 2 for x in x_pos], pg_vals, width=width, color=ENGINE_COLORS["postgresql"], label="PostgreSQL 18.6")
            ax.set_xticks(x_pos)
            ax.set_xticklabels(labels_x, fontsize=7.5)
        ax.set_title(title, fontsize=10, fontweight="bold")
        ax.set_ylabel(ylabel)
        ax.grid(True, axis="y", linestyle="--", alpha=0.4)
        if idx == 0:
            ax.legend(fontsize=8)
    fig.suptitle("M3: Eficiencia de Recursos Normalizada por cada 1 000 Documentos Procesados", fontsize=12, fontweight="bold", y=0.99)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    f4 = fig_dir / "fig4_m3_efficiency.png"
    fig.savefig(f4, dpi=180)
    plt.close(fig)
    generated.append(str(f4))

    return generated


def main():
    parser = argparse.ArgumentParser(description="Analiza mediciones.csv, anexo_variantes.csv y meta.json de la campaña.")
    parser.add_argument("--results-dir", default=str(ROOT / "results"), help="Directorio de resultados (default: results)")
    parser.add_argument("--out-dir", default=str(ROOT / "results" / "analysis"), help="Directorio de salida")
    parser.add_argument("--include-pilot", action="store_true", help="Incluye corridas válidas de results/pilot/")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_rows(results_dir, include_pilot=args.include_pilot)
    cells = aggregate_cells(rows, results_dir)
    m1_rows = compute_m1_degradation(cells)

    summary_payload = {
        "total_rows_loaded": len(rows),
        "total_cells_aggregated": len(cells),
        "cells": cells,
        "m1_degradation": m1_rows,
    }
    (out_dir / "summary_metrics.json").write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "tables_summary.md").write_text(
        generate_markdown_tables(cells, m1_rows), encoding="utf-8"
    )
    figs = plot_figures(cells, out_dir / "figures")
    print(json.dumps({
        "rows_loaded": len(rows),
        "cells_aggregated": len(cells),
        "summary_json": str(out_dir / "summary_metrics.json"),
        "tables_md": str(out_dir / "tables_summary.md"),
        "figures": figs,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
