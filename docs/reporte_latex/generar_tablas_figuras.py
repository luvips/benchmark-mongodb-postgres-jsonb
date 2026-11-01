"""Agrega las 180 corridas reales y produce tablas LaTeX, figuras y un JSON de cifras para el reporte."""
import csv
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Ejecutar desde la raíz del repo:  python docs/reporte_latex/generar_tablas_figuras.py
OUT = Path(__file__).resolve().parent
D = OUT.parents[1] / "results"
FIG = OUT / "figuras"
FIG.mkdir(parents=True, exist_ok=True)

PROF = {"PROFILE_A_BASELINE": "A", "PROFILE_B_MEDIUM": "B", "PROFILE_C_SEVERE": "C"}
ENG = {"mongodb": "MongoDB", "postgresql": "PostgreSQL"}


def fnum(v):
    return None if v in ("", None) else float(v)


rows = []
for name in ("mediciones.csv", "anexo_variantes.csv"):
    with open(D / name, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["_file"] = name
            q = r["query_id"]
            base = q.split("_")[0]
            variant = "relaxed" if q.endswith("_relaxed") else "strict"
            rid = f"{r['engine']}-{PROF[r['profile']]}-{base}-c{r['concurrency']}-r{r['run_number']}" + ("-relaxed" if variant == "relaxed" else "")
            r["_meta"] = json.load(open(D / "raw" / rid / "meta.json", encoding="utf-8"))
            r["_rid"] = rid
            r["_base"] = base
            r["_variant"] = variant
            rows.append(r)
assert len(rows) == 180, len(rows)


def med(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return (None, None, None)
    return (st.median(vals), min(vals), max(vals))


cells = defaultdict(list)
for r in rows:
    cells[(r["engine"], PROF[r["profile"]], r["_base"], r["_variant"], int(r["concurrency"]))].append(r)

agg = {}
for k, rs in cells.items():
    m = [r["_meta"] for r in rs]
    statuses = defaultdict(int)
    for r in rs:
        statuses[r["status"]] += 1
    agg[k] = {
        "n_runs": len(rs),
        "qps": med([fnum(r["throughput_qps"]) for r in rs]),
        "p50": med([fnum(r["latency_p50_ms"]) for r in rs]),
        "p95": med([fnum(r["latency_p95_ms"]) for r in rs]),
        "p99": med([fnum(r["latency_p99_ms"]) for r in rs]),
        "thr": med([fnum(r["cpu_throttled_seconds"]) for r in rs]),
        "rss": med([fnum(r["max_rss_mb"]) for r in rs]),
        "cpu1k": med([x.get("m3_cpu_seconds_per_1k_docs") for x in m]),
        "mempeak": med([x.get("m3_memory_peak_mb") for x in m]),
        "mem1k": med([x.get("m3_memory_mb_s_per_1k_docs") for x in m]),
        "cpu_s": med([x.get("cpu_usage_seconds") for x in m]),
        "n_min": min(x["n_completed"] for x in m),
        "n_med": st.median(x["n_completed"] for x in m),
        "cens": sum(x["n_censored"] for x in m),
        "oomk": sum(x["signals"]["oom_kills"] for x in m),
        "wbytes": med([x.get("written_bytes_window") for x in m]),
        "temp_bytes": med([(x["after"].get("temp_bytes", 0) - x["before_window"].get("temp_bytes", 0)) if x["after"] else None for x in m]),
        "statuses": dict(statuses),
        "spill_runs": sum(1 for r in rs if r["disk_spill"] == "true"),
        "cg_samples": min(x["cgroup_window_samples"] for x in m),
        "prom_samples": min(x["prometheus_samples"] for x in m),
        "noisy": sum(1 for x in m if x["noisy"]),
    }

# ---------------- utilidades de formato ----------------

def f(v, d=1):
    if v is None:
        return "---"
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(",", "\\,")
    return f"{v:.{d}f}"


def fs(v):  # ms -> s
    return "---" if v is None else (f"{v/1000:.2f}" if v < 100000 else f"{v/1000:.1f}")


def status_str(sts):
    order = ["ok", "spill", "oom", "error"]
    return ", ".join(f"{s}:{sts[s]}" for s in order if s in sts)


# ---------------- tablas principales (strict) ----------------

def table_results(query, variant="strict", profiles=("A", "B", "C")):
    lines = []
    for eng in ("mongodb", "postgresql"):
        for p in profiles:
            for c in (1, 10, 50):
                a = agg.get((eng, p, query, variant, c))
                if not a:
                    continue
                q, p50, p95, p99 = a["qps"], a["p50"], a["p95"], a["p99"]
                p99s = fs(p99[0]) + (f" [{fs(p99[1])}--{fs(p99[2])}]" if p99[0] is not None else "")
                nflag = "" if a["n_min"] >= 100 else "$^{\\dagger}$"
                lines.append(
                    f"{ENG[eng]} & {p} & {c} & {f(q[0],3)} & {fs(p50[0])} & {fs(p95[0])} & {p99s}{nflag} & "
                    f"{a['n_min']} & {f(a['thr'][0],1)} & {f(a['rss'][0],0)} & {status_str(a['statuses'])} \\\\"
                )
            lines.append("\\midrule" if p != profiles[-1] else "")
        lines.append("\\midrule")
    return "\n".join(l for l in lines if l).rstrip("\\midrule").rstrip()


def write(name, text):
    (OUT / "tablas").mkdir(exist_ok=True)
    (OUT / "tablas" / name).write_text(text + "\n", encoding="utf-8")


for q in ("Q1", "Q2"):
    body = []
    for eng in ("mongodb", "postgresql"):
        for p in ("A", "B", "C"):
            for c in (1, 10, 50):
                a = agg[(eng, p, q, "strict", c)]
                p99 = a["p99"]
                p99s = fs(p99[0]) + (f" [{fs(p99[1])}--{fs(p99[2])}]" if p99[0] is not None else "")
                nflag = "" if a["n_min"] >= 100 else "$^{\\dagger}$"
                body.append(
                    f"{ENG[eng] if (p=='A' and c==1) else ''} & {p if c==1 else ''} & {c} & {f(a['qps'][0],3)} & {fs(a['p50'][0])} & "
                    f"{fs(a['p95'][0])} & {p99s}{nflag} & {a['n_min']} & {f(a['thr'][0],1)} & {f(a['rss'][0],0)} & {status_str(a['statuses'])} \\\\"
                )
            if p != "C":
                body.append("\\cmidrule(lr){2-11}")
        if eng == "mongodb":
            body.append("\\midrule")
    write(f"res_{q}.tex", "\n".join(body))

# ---------------- anexo relaxed vs strict ----------------
body = []
for q, profs in (("Q1", ("C",)), ("Q2", ("A", "B", "C"))):
    for eng in ("mongodb", "postgresql"):
        for p in profs:
            for c in (1, 10, 50):
                s = agg[(eng, p, q, "strict", c)]
                r = agg[(eng, p, q, "relaxed", c)]
                dq = None
                if s["qps"][0] and r["qps"][0] is not None and s["qps"][0] > 0:
                    dq = 100 * (r["qps"][0] - s["qps"][0]) / s["qps"][0]
                body.append(
                    f"{q} & {ENG[eng]} & {p} & {c} & {f(s['qps'][0],3)} & {f(r['qps'][0],3)} & "
                    f"{('---' if dq is None else f'{dq:+.0f}\\,\\%')} & {fs(s['p99'][0])} & {fs(r['p99'][0])} & "
                    f"{status_str(s['statuses'])} & {status_str(r['statuses'])} \\\\"
                )
        body.append("\\midrule")
write("anexo_relaxed.tex", "\n".join(body).rstrip("\\midrule").rstrip())

# ---------------- M1: degradación respecto de A ----------------
body = []
m1 = {}
for q in ("Q1", "Q2"):
    for eng in ("mongodb", "postgresql"):
        for c in (1, 10, 50):
            a = agg[(eng, "A", q, "strict", c)]
            row = [f"{q}", ENG[eng], str(c), fs(a["p99"][0])]
            for p in ("B", "C"):
                b = agg[(eng, p, q, "strict", c)]
                if a["p99"][0] and b["p99"][0]:
                    d = 100 * (b["p99"][0] - a["p99"][0]) / a["p99"][0]
                    row += [fs(b["p99"][0]), f"{d:+.0f}\\,\\%"]
                    m1[(q, eng, c, p)] = d
                else:
                    row += [fs(b["p99"][0]), "OOM" if b["statuses"].get("oom") else "---"]
                row += [f(b["thr"][0], 0)]
            body.append(" & ".join(row) + " \\\\")
        body.append("\\midrule")
write("m1.tex", "\n".join(body).rstrip("\\midrule").rstrip())

# ---------------- M3 ----------------
body = []
for q in ("Q1", "Q2"):
    for c in (1, 10, 50):
        for p in ("A", "B", "C"):
            mo = agg[("mongodb", p, q, "strict", c)]
            pg = agg[("postgresql", p, q, "strict", c)]
            def g(a, k, d):
                return f(a[k][0] * (1000 if k == "cpu1k" else 1), d) if a[k][0] is not None else "---"
            body.append(
                f"{q if (c==1 and p=='A') else ''} & {c if p=='A' else ''} & {p} & {g(mo,'cpu1k',2)} & {g(pg,'cpu1k',2)} & "
                f"{g(mo,'mempeak',0)} & {g(pg,'mempeak',0)} & {g(mo,'mem1k',2)} & {g(pg,'mem1k',2)} \\\\"
            )
        if c != 50:
            body.append("\\cmidrule(lr){2-9}")
    if q == "Q1":
        body.append("\\midrule")
write("m3.tex", "\n".join(body))

# ---------------- M2: spill / OOM ----------------
body = []
for q in ("Q1", "Q2"):
    for eng in ("mongodb", "postgresql"):
        for p in ("A", "B", "C"):
            for v in ("strict", "relaxed"):
                cs = [agg.get((eng, p, q, v, c)) for c in (1, 10, 50)]
                if not all(cs):
                    continue
                cells_s = []
                for a in cs:
                    s = status_str(a["statuses"])
                    tb = a["temp_bytes"][0]
                    extra = f" ({tb/2**30:.0f}\\,GiB)" if (tb and tb > 2**30) else ""
                    cells_s.append(s + extra)
                body.append(f"{q} & {ENG[eng]} & {p} & {v} & " + " & ".join(cells_s) + " \\\\")
    body.append("\\midrule")
write("m2.tex", "\n".join(body).rstrip("\\midrule").rstrip())

# ---------------- calidad de telemetría por corrida ----------------
qual = {
    "runs": len(rows),
    "min_prom": min(r["_meta"]["prometheus_samples"] for r in rows),
    "min_cg": min(r["_meta"]["cgroup_window_samples"] for r in rows),
    "med_cg": st.median(r["_meta"]["cgroup_window_samples"] for r in rows),
    "int_s_min": min(r["_meta"]["engine_mem_integral_seconds"] for r in rows),
    "int_s_max": max(r["_meta"]["engine_mem_integral_seconds"] for r in rows),
    "noisy": sum(1 for r in rows if r["_meta"]["noisy"]),
    "status": {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "spill", "oom", "error")},
    "strict_status": {s: sum(1 for r in rows if r["status"] == s and r["_variant"] == "strict") for s in ("ok", "spill", "oom", "error")},
    "relaxed_status": {s: sum(1 for r in rows if r["status"] == s and r["_variant"] == "relaxed") for s in ("ok", "spill", "oom", "error")},
    "p99_unreliable_cells": sum(1 for a in agg.values() if a["n_min"] < 100),
    "cells": len(agg),
    "versions": sorted({r["_meta"]["engine_version"] for r in rows}),
}

# ---------------- figuras ----------------
plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.3, "font.family": "serif"})
COL = {"mongodb": "#2E7D32", "postgresql": "#1565C0"}
LS = {"A": "-", "B": "--", "C": ":"}
MK = {"A": "o", "B": "s", "C": "^"}

# Fig 1: P99 vs concurrencia
fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2), sharey=False)
for ax, q in zip(axes, ("Q1", "Q2")):
    for eng in ("mongodb", "postgresql"):
        for p in ("A", "B", "C"):
            xs, ys, lo, hi, oom = [], [], [], [], []
            for c in (1, 10, 50):
                a = agg[(eng, p, q, "strict", c)]
                if a["p99"][0] is None:
                    oom.append(c)
                    continue
                xs.append(c); ys.append(a["p99"][0] / 1000)
                lo.append((a["p99"][0] - a["p99"][1]) / 1000); hi.append((a["p99"][2] - a["p99"][0]) / 1000)
            ax.errorbar(xs, ys, yerr=[lo, hi], color=COL[eng], ls=LS[p], marker=MK[p], ms=4, capsize=2, lw=1.2,
                        label=f"{ENG[eng]} {p}")
            for c in oom:
                ax.scatter([c], [400], marker="x", color=COL[eng], s=45, zorder=5)
                ax.annotate(f"OOM ({p})", (c, 400), textcoords="offset points", xytext=(-38 if p == "B" else 4, 4 if p == "B" else -12), fontsize=6.5, color=COL[eng])
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xticks([1, 10, 50]); ax.set_xticklabels(["1", "10", "50"])
    ax.set_xlabel("Clientes concurrentes (c)"); ax.set_title(f"{q} (variante strict)")
axes[0].set_ylabel("Latencia P99 (s, escala log)")
h, l = axes[0].get_legend_handles_labels()
fig.legend(h, l, loc="lower center", ncol=6, fontsize=7, frameon=False)
fig.tight_layout(rect=(0, 0.08, 1, 1))
fig.savefig(FIG / "fig1_p99_concurrencia.pdf"); plt.close(fig)

# Fig 2: throttling vs QPS
fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0))
for ax, q in zip(axes, ("Q1", "Q2")):
    for r in rows:
        if r["_base"] != q or r["_variant"] != "strict":
            continue
        p = PROF[r["profile"]]
        ax.scatter(fnum(r["throughput_qps"]), fnum(r["cpu_throttled_seconds"]), color=COL[r["engine"]],
                   marker=MK[p], s=18, alpha=0.75, edgecolor="none")
    ax.set_xlabel("Throughput (QPS)"); ax.set_title(q)
axes[0].set_ylabel("CPU throttled en la ventana (s)")
from matplotlib.lines import Line2D
leg = [Line2D([], [], color=COL[e], marker="o", ls="", label=ENG[e]) for e in COL] + \
      [Line2D([], [], color="gray", marker=MK[p], ls="", label=f"Perfil {p}") for p in MK]
fig.legend(handles=leg, loc="lower center", ncol=5, fontsize=7, frameon=False)
fig.tight_layout(rect=(0, 0.08, 1, 1))
fig.savefig(FIG / "fig2_throttling_qps.pdf"); plt.close(fig)

# Fig 3: series de memoria (anon+shmem y file) en Q1 c=50 perfiles B y C, rep 1


def series(rid):
    t, eng, fil = [], [], []
    with open(D / "raw" / rid / "series.csv", encoding="utf-8") as fh:
        rd = list(csv.DictReader(fh))
    if not rd:
        return t, eng, fil
    t0 = float(rd[0]["t"])
    for x in rd:
        t.append(float(x["t"]) - t0)
        eng.append((int(x["anon"]) + int(x["shmem"])) / 2**20)
        fil.append(int(x["file"]) / 2**20)
    return t, eng, fil


fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.7), sharey=True)
for ax, (p, lim) in zip(axes, (("A", 4096), ("B", 2048), ("C", 1024))):
    for eng in ("mongodb", "postgresql"):
        t, e, fl = series(f"{eng}-{p}-Q1-c50-r1")
        ax.plot(t, e, color=COL[eng], lw=1.1, label=f"{ENG[eng]} anon+shmem")
        ax.plot(t, fl, color=COL[eng], lw=0.8, ls=":", label=f"{ENG[eng]} page cache")
    ax.axhline(lim, color="crimson", lw=0.8, ls="--")
    ax.axvspan(60, 360, color="gray", alpha=0.08)
    ax.set_title(f"Q1, c=50, perfil {p}"); ax.set_xlabel("Tiempo desde el arranque (s)")
axes[0].set_ylabel("MiB")
h, l = axes[0].get_legend_handles_labels()
fig.legend(h, l, loc="lower center", ncol=4, fontsize=7, frameon=False)
fig.tight_layout(rect=(0, 0.1, 1, 1))
fig.savefig(FIG / "fig3_memoria_q1_c50.pdf"); plt.close(fig)

# Fig 4: M3 CPU_1k y Mem_1k para c=1
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
import numpy as np
labels = [f"{q}-{p}" for q in ("Q1", "Q2") for p in ("A", "B", "C")]
x = np.arange(len(labels)); w = 0.38
for ax, key, title, scale in ((axes[0], "cpu1k", "CPU$_{1k}$ (ms de CPU / 1000 docs), c = 1", 1000), (axes[1], "mem1k", "Mem$_{1k}$ (MB·s / 1000 docs), c = 1", 1)):
    for i, eng in enumerate(("mongodb", "postgresql")):
        vals = [(agg[(eng, l.split('-')[1], l.split('-')[0], 'strict', 1)][key][0] or 0) * scale for l in labels]
        ax.bar(x + (i - 0.5) * w, vals, w, color=COL[eng], label=ENG[eng])
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=8); ax.set_title(title, fontsize=9)
axes[0].legend(fontsize=7)
fig.tight_layout()
fig.savefig(FIG / "fig4_m3_c1.pdf"); plt.close(fig)

# Fig 5: QPS strict vs relaxed en perfil C
fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
for ax, q in zip(axes, ("Q1", "Q2")):
    lab, xs = [], []
    k = 0
    for eng in ("mongodb", "postgresql"):
        for c in (1, 10, 50):
            s = agg[(eng, "C", q, "strict", c)]["qps"][0] or 0
            r = agg[(eng, "C", q, "relaxed", c)]["qps"][0] or 0
            ax.bar(k - 0.2, s, 0.38, color=COL[eng], alpha=0.95, label="strict" if k == 0 else None)
            ax.bar(k + 0.2, r, 0.38, color=COL[eng], alpha=0.45, hatch="//", label="relaxed" if k == 0 else None)
            for off, val, a in ((-0.2, s, agg[(eng, "C", q, "strict", c)]), (0.2, r, agg[(eng, "C", q, "relaxed", c)])):
                if a["statuses"].get("oom"):
                    ax.text(k + off, 0.01, "OOM", rotation=90, ha="center", va="bottom", fontsize=6.5, color="crimson")
            lab.append(f"{'Mo' if eng=='mongodb' else 'PG'}\nc={c}"); k += 1
    ax.set_xticks(range(k)); ax.set_xticklabels(lab, fontsize=7); ax.set_title(f"{q}, perfil C"); ax.set_ylabel("QPS")
axes[0].legend(fontsize=7)
fig.tight_layout()
fig.savefig(FIG / "fig5_strict_relaxed_C.pdf"); plt.close(fig)

# ---------------- cifras para el texto ----------------
def A(e, p, q, v, c):
    return agg[(e, p, q, v, c)]


facts = {"quality": qual, "cells": {}}
for k, a in agg.items():
    facts["cells"]["|".join(map(str, k))] = {kk: vv for kk, vv in a.items()}
json.dump(facts, open(OUT / "cifras.json", "w"), indent=1, default=str, ensure_ascii=False)
print(json.dumps(qual, indent=1, ensure_ascii=False))
for k in sorted(agg):
    a = agg[k]
    print(k, "qps", f(a["qps"][0], 3), "p99", fs(a["p99"][0]), "n", a["n_min"], "thr", f(a["thr"][0], 0), "rss", f(a["rss"][0], 0),
          "mem", f(a["mempeak"][0], 0), "cpu1k", a["cpu1k"][0] and round(a["cpu1k"][0]*1000, 2), "mem1k", a["mem1k"][0] and round(a["mem1k"][0], 2),
          a["statuses"], "oomk", a["oomk"], "cens", a["cens"], "tempGiB", a["temp_bytes"][0] and round(a["temp_bytes"][0]/2**30, 1))
