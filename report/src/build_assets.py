#!/usr/bin/env python3
"""
report/src/build_assets.py
==========================
Genera, a partir de los resultados REALES de la campaña, todo lo que consume
report/reporte_tecnico.tex:

  report/figures/*.pdf        figuras vectoriales (estilo APA: sin título interno, escala de grises)
  report/tables/*.tex         tablas con booktabs (estilo APA: solo reglas horizontales)
  report/numbers.tex          macros \\fact{clave} con las cifras citadas en el texto
  report/src/facts.txt        las mismas cifras en texto plano, para revisión

Solo LEE results/ (nunca escribe en él). Entradas:
  results/mediciones.csv, results/anexo_variantes.csv,
  results/raw/<run_id>/{meta.json,series.csv}, results/explain/*.txt, state/journal.jsonl

Uso (desde la raíz del repo):  ./.venv/Scripts/python.exe report/src/build_assets.py
"""
from __future__ import annotations

import csv
import json
import re
import statistics as st
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib import ticker  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"
OUT = ROOT / "report"
FIG = OUT / "figures"
TAB = OUT / "tables"
for d in (FIG, TAB):
    d.mkdir(parents=True, exist_ok=True)

PROFILES = ["A", "B", "C"]
CONCS = [1, 10, 50]
QUERIES = ["Q1", "Q2"]
ENGINES = ["mongodb", "postgresql"]
ENG_SHORT = {"mongodb": "Mongo", "postgresql": "PG"}
ENG_LABEL = {"mongodb": "MongoDB 9.0.4", "postgresql": "PostgreSQL 18.6"}
PLETTER = {"PROFILE_A_BASELINE": "A", "PROFILE_B_MEDIUM": "B", "PROFILE_C_SEVERE": "C"}
SESSIONS = 300.0  # s de ventana de medición

# ----------------------------------------------------------------------------- estilo APA (figuras)
avail = {f.name for f in font_manager.fontManager.ttflist}
FONT = next((f for f in ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans") if f in avail), "DejaVu Sans")
plt.rcParams.update({
    "font.family": FONT, "font.size": 9, "axes.labelsize": 9, "axes.titlesize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.linewidth": 0.8, "pdf.fonttype": 42, "ps.fonttype": 42,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.03,
})
STY = {  # distinguible en blanco y negro
    "mongodb": dict(color="black", marker="o", ls="-", label=ENG_LABEL["mongodb"]),
    "postgresql": dict(color="0.45", marker="s", ls="--", label=ENG_LABEL["postgresql"]),
}
PROF_FILL = {"A": "white", "B": "0.65", "C": "black"}


# ----------------------------------------------------------------------------- carga de datos
def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load():
    runs = []
    for fname in ("mediciones.csv", "anexo_variantes.csv"):
        with open(RES / fname, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                q = r["query_id"]
                base, relaxed = (q.split("_")[0], True) if q.endswith("_relaxed") else (q, False)
                L = PLETTER[r["profile"]]
                c, rep = int(r["concurrency"]), int(r["run_number"])
                rid = f"{r['engine']}-{L}-{base}-c{c}-r{rep}" + ("-relaxed" if relaxed else "")
                meta = {}
                mp = RES / "raw" / rid / "meta.json"
                if mp.exists():
                    meta = json.loads(mp.read_text(encoding="utf-8"))
                runs.append(dict(
                    engine=r["engine"], L=L, q=base, relaxed=relaxed, c=c, rep=rep, rid=rid,
                    qps=fnum(r["throughput_qps"]), p50=fnum(r["latency_p50_ms"]), p95=fnum(r["latency_p95_ms"]),
                    p99=fnum(r["latency_p99_ms"]), thr=fnum(r["cpu_throttled_seconds"]), rss=fnum(r["max_rss_mb"]),
                    spill=r["disk_spill"].strip().lower() == "true", status=r["status"],
                    n=meta.get("n_completed"), cpu1k=meta.get("m3_cpu_seconds_per_1k_docs"),
                    memk=meta.get("m3_memory_mb_s_per_1k_docs"), mempeak=meta.get("m3_memory_peak_mb"),
                    oomk=(meta.get("signals") or {}).get("oom_kills"),
                ))
    return runs


def med(vals):
    v = [x for x in vals if x is not None]
    return st.median(v) if v else None


def cell_agg(runs):
    g = defaultdict(list)
    for r in runs:
        g[(r["engine"], r["L"], r["q"], r["relaxed"], r["c"])].append(r)
    out = {}
    for k, rs in g.items():
        p99s = [r["p99"] for r in rs if r["p99"] is not None]
        qps = [r["qps"] for r in rs if r["qps"] is not None]
        ns = [r["n"] for r in rs if r["n"] is not None]
        stat = {r["status"] for r in rs}
        out[k] = dict(
            reps=len(rs), status=("/".join(sorted(stat))), nstat=len(stat),
            spill=sum(r["spill"] for r in rs), oomk=sum((r["oomk"] or 0) for r in rs),
            qps=med(qps), qps_min=min(qps) if qps else None, qps_max=max(qps) if qps else None,
            p50=med([r["p50"] for r in rs]), p95=med([r["p95"] for r in rs]), p99=med(p99s),
            p99_min=min(p99s) if p99s else None, p99_max=max(p99s) if p99s else None,
            thr=med([r["thr"] for r in rs]), rss=med([r["rss"] for r in rs]),
            n=min(ns) if ns else None, cpu1k=med([r["cpu1k"] for r in rs]), memk=med([r["memk"] for r in rs]),
            mempeak=med([r["mempeak"] for r in rs]),
        )
    return out


# ----------------------------------------------------------------------------- formato
def thou(x, d=0):
    s = f"{x:,.{d}f}"
    return s.replace(",", r"\,")


def fm(x, d=0, dash="---"):
    return dash if x is None else thou(x, d)


def f3(x):
    return "---" if x is None else f"{x:.3f}"


STATUS_ES = {"ok": "ok", "spill": "spill", "oom": "OOM"}


def st_es(s):
    return "/".join(STATUS_ES.get(p, p) for p in s.split("/"))


def write(path, text):
    path.write_text(text, encoding="utf-8")


# ----------------------------------------------------------------------------- tablas
def table_results(A, q):
    rows = []
    for L in PROFILES:
        for c in CONCS:
            for e in ENGINES:
                a = A.get((e, L, q, False, c))
                if not a:
                    continue
                reliable = a["n"] is not None and a["n"] >= 100
                p99 = fm(a["p99"])
                if a["p99"] is not None and not reliable:
                    p99 += r"$^{\dagger}$"
                rng = "---" if a["p99_min"] is None else f"[{thou(a['p99_min'])}; {thou(a['p99_max'])}]"
                rows.append(
                    f"{L} & {c} & {ENG_SHORT[e]} & {f3(a['qps'])} & {fm(a['p50'])} & {fm(a['p95'])} & {p99} & {rng} & "
                    f"{a['n'] if a['n'] is not None else '---'} & {fm(a['thr'], 1)} & {fm(a['rss'], 0)} & {st_es(a['status'])} \\\\"
                )
        rows.append(r"\addlinespace[2pt]")
    body = "\n".join(rows[:-1])
    return (r"\begin{tabular}{@{}llllrrrlrrrl@{}}" "\n" r"\toprule" "\n"
            r"Perf. & $c$ & Motor & QPS & P50 & P95 & P99 & [P99 mín.; máx.] & $N_{\min}$ & Thr. (s) & RSS (MB) & Estado \\" "\n"
            r"\midrule" "\n" + body + "\n" r"\bottomrule" "\n" r"\end{tabular}" "\n")


def table_relaxed(A):
    rows = []
    for q, Ls in (("Q1", ["C"]), ("Q2", PROFILES)):
        for L in Ls:
            for c in CONCS:
                for e in ENGINES:
                    s, rx = A.get((e, L, q, False, c)), A.get((e, L, q, True, c))
                    if not s or not rx:
                        continue

                    def pair(k, d=0):
                        return f"{fm(s[k], d)} / {fm(rx[k], d)}"
                    qs = f"{f3(s['qps'])} / {f3(rx['qps'])}"
                    rows.append(f"{q} & {L} & {c} & {ENG_SHORT[e]} & {qs} & {pair('p99')} & "
                                f"{st_es(s['status'])} / {st_es(rx['status'])} \\\\")
        rows.append(r"\addlinespace[2pt]")
    return (r"\begin{tabular}{@{}lllllll@{}}" "\n" r"\toprule" "\n"
            r"Consulta & Perfil & $c$ & Motor & QPS (estricta / relajada) & P99 ms (estricta / relajada) & Estado \\" "\n"
            r"\midrule" "\n" + "\n".join(rows[:-1]) + "\n" r"\bottomrule" "\n" r"\end{tabular}" "\n")


def table_status(runs):
    cnt = defaultdict(lambda: defaultdict(int))
    for r in runs:
        key = (r["q"] + ("_rel" if r["relaxed"] else ""), r["engine"], r["L"])
        cnt[key][r["status"]] += 1
    rows = []
    for qv in ("Q1", "Q2", "Q1_rel", "Q2_rel"):
        for e in ENGINES:
            cells = []
            for L in PROFILES:
                c = cnt.get((qv, e, L))
                if not c:
                    cells.append("---")
                    continue
                cells.append(" / ".join(f"{c.get(s, 0)}" for s in ("ok", "spill", "oom")))
            if all(x == "---" for x in cells):
                continue
            lab = {"Q1": "Q1 estricta", "Q2": "Q2 estricta", "Q1_rel": "Q1 relajada", "Q2_rel": "Q2 relajada"}[qv]
            rows.append(f"{lab} & {ENG_SHORT[e]} & " + " & ".join(cells) + r" \\")
        rows.append(r"\addlinespace[2pt]")
    tot = defaultdict(int)
    for r in runs:
        tot[r["status"]] += 1
    rows[-1] = r"\midrule"
    rows.append(f"Total (180) & & \\multicolumn{{3}}{{c}}{{{tot['ok']} / {tot['spill']} / {tot['oom']}}} \\\\")
    return (r"\begin{tabular}{@{}llccc@{}}" "\n" r"\toprule" "\n"
            r"Carga & Motor & Perfil A & Perfil B & Perfil C \\" "\n" r"\midrule" "\n"
            + "\n".join(rows) + "\n" r"\bottomrule" "\n" r"\end{tabular}" "\n")


def table_m1(A):
    rows = []
    for q in QUERIES:
        for e in ENGINES:
            for c in CONCS:
                base = A.get((e, "A", q, False, c))
                for L in ("B", "C"):
                    a = A.get((e, L, q, False, c))
                    if not base or not a:
                        continue

                    def dlt(x, y):
                        return "---" if (x is None or y is None or x == 0) else f"{(y - x) / x * 100:+.0f}\\%"
                    rows.append(f"{q} & {ENG_SHORT[e]} & {c} & {L} & {f3(base['qps'])} & {f3(a['qps'])} & "
                                f"{dlt(base['qps'], a['qps'])} & {fm(base['p99'])} & {fm(a['p99'])} & "
                                f"{dlt(base['p99'], a['p99'])} & {fm(a['thr'], 1)} \\\\")
        rows.append(r"\addlinespace[2pt]")
    return (r"\begin{longtable}{@{}lllllrrrrrr@{}}" "\n"
            r"\caption{Variación del rendimiento y del P99 de los perfiles B y C respecto del perfil A}\label{tab:m1}\\" "\n"
            r"\toprule" "\n"
            r"Q & Motor & $c$ & Perf. & QPS A & QPS & $\Delta$QPS & P99 A & P99 & $\Delta$P99 & Thr. (s) \\" "\n"
            r"\midrule\endfirsthead" "\n" r"\toprule" "\n"
            r"Q & Motor & $c$ & Perf. & QPS A & QPS & $\Delta$QPS & P99 A & P99 & $\Delta$P99 & Thr. (s) \\" "\n"
            r"\midrule\endhead" "\n" r"\bottomrule\endlastfoot" "\n" + "\n".join(rows[:-1]) + "\n" r"\end{longtable}" "\n")


def table_m3(A):
    rows = []
    for q in QUERIES:
        for L in PROFILES:
            for c in CONCS:
                m, p = A.get(("mongodb", L, q, False, c)), A.get(("postgresql", L, q, False, c))
                if not m or not p:
                    continue

                def cpu(x):
                    return "---" if x is None else f"{x:.4f}"

                def rat(a, b):
                    return "---" if (a is None or b is None or a == 0) else f"{b / a:.1f}"
                rows.append(f"{q} & {L} & {c} & {cpu(m['cpu1k'])} & {cpu(p['cpu1k'])} & {rat(m['cpu1k'], p['cpu1k'])} & "
                            f"{fm(m['mempeak'], 0)} & {fm(p['mempeak'], 0)} & {fm(m['memk'], 2)} & {fm(p['memk'], 2)} & "
                            f"{rat(m['memk'], p['memk'])} \\\\")
        rows.append(r"\addlinespace[2pt]")
    hdr = (r"Q & Perf. & $c$ & CPU Mongo & CPU PG & PG/Mongo & Pico Mongo & Pico PG & Int. Mongo & Int. PG & PG/Mongo \\")
    return (r"\begin{longtable}{@{}lllrrrrrrrr@{}}" "\n"
            r"\caption{CPU, pico e integral de memoria por cada 1\,000 documentos procesados}\label{tab:m3}\\" "\n"
            r"\toprule" "\n" + hdr + "\n" r"\midrule\endfirsthead" "\n"
            r"\toprule" "\n" + hdr + "\n" r"\midrule\endhead" "\n" r"\bottomrule\endlastfoot" "\n"
            + "\n".join(rows[:-1]) + "\n" r"\end{longtable}" "\n")


# ----------------------------------------------------------------------------- planes de ejecución
def parse_pg_plan(txt):
    d = {}
    m = re.search(r"Execution Time:\s*([\d.]+) ms", txt)
    d["ms"] = float(m.group(1)) if m else None
    d["seq"] = "Parallel Seq Scan" in txt or "Seq Scan" in txt
    d["idx"] = "Index" in txt and "Scan using" in txt
    d["topn"] = "top-N heapsort" in txt
    disk = [int(x) for x in re.findall(r"Sort Method: external merge\s+Disk: (\d+)kB", txt)]
    d["disk_mb"] = sum(disk) / 1024 if disk else 0.0
    tw = [int(x) for x in re.findall(r"temp read=\d+ written=(\d+)", txt)]
    d["temp_blocks"] = max(tw) if tw else 0
    m = re.search(r"Workers Launched:\s*(\d+)", txt)
    d["workers"] = int(m.group(1)) if m else 0
    d["agg"] = "GroupAggregate" if "GroupAggregate" in txt else ("HashAggregate" if "HashAggregate" in txt else "—")
    m = re.search(r"GroupAggregate\s+\(cost=[\d.]+\.\.[\d.]+ rows=(\d+)", txt)
    d["est_rows"] = int(m.group(1)) if m else None
    return d


def parse_mongo_plan(txt):
    d = dict(ms=None, docs=None, keys=None, used_disk=None, spills=None, peak=None, stage=None)
    try:
        j = json.loads(txt)
    except Exception:
        return d
    if "stages" in j:  # agregación por etapas (Q1): las estadísticas viven en $cursor y en la última etapa
        cur = j["stages"][0].get("$cursor", {})
        es = cur.get("executionStats", {})
        d["ms"] = j["stages"][-1].get("executionTimeMillisEstimate")
        d["docs"], d["keys"] = es.get("totalDocsExamined"), es.get("totalKeysExamined")
        j.setdefault("queryPlanner", cur.get("queryPlanner", {}))
    else:
        es = j.get("executionStats", {})
        d["ms"], d["docs"], d["keys"] = es.get("executionTimeMillis"), es.get("totalDocsExamined"), es.get("totalKeysExamined")

    def walk(o):
        if isinstance(o, dict):
            if "usedDisk" in o:
                d["used_disk"], d["spills"] = o.get("usedDisk"), o.get("spills")
                if o.get("peakTrackedMemBytes") is not None:
                    d["peak"] = max(d["peak"] or 0, o["peakTrackedMemBytes"])
            if "$group" in str(list(o.keys())) and "peakTrackedMemBytes" in o:
                d["peak"] = max(d["peak"] or 0, o["peakTrackedMemBytes"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(j)
    win = j.get("queryPlanner", {}).get("winningPlan", {})
    s = json.dumps(win)
    d["stage"] = "IXSCAN+FETCH" if "IXSCAN" in s else ("COLLSCAN" if "COLLSCAN" in s else "?")
    return d


def table_plans():
    rows, facts = [], {}
    for q in QUERIES:
        for L in ("A", "C"):
            pg = parse_pg_plan((RES / "explain" / f"postgresql-{L}-{q}.txt").read_text(encoding="utf-8"))
            mg = parse_mongo_plan((RES / "explain" / f"mongodb-{L}-{q}.txt").read_text(encoding="utf-8"))
            facts[(q, L)] = (pg, mg)
            scan = "Seq Scan paralelo" if pg["seq"] and not pg["idx"] else "Index Scan"
            sort = "top-N heapsort" if pg["topn"] else ("external merge" if pg["disk_mb"] > 0 else "---")
            rows.append(
                f"{q} & {L} & PG & {scan} & {sort} & {fm(pg['disk_mb'], 0)} & {fm(pg['ms'], 0)} & "
                f"{pg['workers']} \\\\")
            peak = "---" if mg["peak"] is None else (f"{mg['peak'] / 1024:.1f} KiB" if mg["peak"] < 1e6 else f"{mg['peak'] / 1e6:.2f} MB")
            rows.append(
                f"{q} & {L} & Mongo & {mg['stage']} & {('disco' if mg['used_disk'] else 'memoria')} ({peak}) & "
                f"0 & {fm(mg['ms'], 0)} & --- \\\\")
        rows.append(r"\addlinespace[2pt]")
    t = (r"\begin{tabular}{@{}lllllrrr@{}}" "\n" r"\toprule" "\n"
         r"Q & Perf. & Motor & Acceso a datos & Ordenación/agrupación & Spill (MiB) & Tiempo (ms) & Workers \\" "\n"
         r"\midrule" "\n" + "\n".join(rows[:-1]) + "\n" r"\bottomrule" "\n" r"\end{tabular}" "\n")
    return t, facts


def plan_listing():
    txt = (RES / "explain" / "postgresql-C-Q1.txt").read_text(encoding="utf-8").splitlines()
    keep = []
    for ln in txt:
        if re.search(r"^(GroupAggregate|\s+-> )|Sort Method|Sort Key|Execution Time|Workers Launched|Presorted|Pre-sorted|Full-sort", ln):
            s = ln.rstrip()
            s = re.sub(r"\(cost=[^)]*\)\s*", "", s)
            s = re.sub(r"\(actual time=([\d.]+)\.\.([\d.]+) rows=([\d.]+) loops=(\d+)\)", r"[t=\2ms rows=\3 x\4]", s)
            keep.append(s[:100] + ("..." if len(s) > 100 else ""))
    return "\n".join(keep[:26])


# ----------------------------------------------------------------------------- figuras
PREV = OUT / "src" / "preview"
PREV.mkdir(parents=True, exist_ok=True)


def save(fig, name):
    fig.savefig(FIG / f"{name}.pdf")
    fig.savefig(PREV / f"{name}.png", dpi=140)


def fig_p99(A):
    fig, axes = plt.subplots(2, 3, figsize=(6.5, 4.6), sharex=True)
    for i, q in enumerate(QUERIES):
        for j, L in enumerate(PROFILES):
            ax = axes[i][j]
            for e in ENGINES:
                xs, ys, lo, hi, rel = [], [], [], [], []
                for c in CONCS:
                    a = A.get((e, L, q, False, c))
                    if a and a["p99"] is not None:
                        xs.append(c)
                        ys.append(a["p99"])
                        lo.append(a["p99"] - a["p99_min"])
                        hi.append(a["p99_max"] - a["p99"])
                        rel.append(a["n"] is not None and a["n"] >= 100)
                s = STY[e]
                ax.errorbar(xs, ys, yerr=[lo, hi], color=s["color"], ls=s["ls"], lw=1.1, capsize=2, elinewidth=0.8,
                            marker=None)
                for x, y, ok in zip(xs, ys, rel):
                    ax.plot([x], [y], marker=s["marker"], color=s["color"], ms=5.5,
                            mfc=s["color"] if ok else "white", mew=1.0, ls="none")
                # marca OOM: celda sin P99
                for c in CONCS:
                    a = A.get((e, L, q, False, c))
                    if a and a["p99"] is None and "oom" in a["status"]:
                        ax.plot([c], [0.08], marker="X", color=s["color"], ms=7, ls="none",
                                transform=ax.get_xaxis_transform(), clip_on=False)
                        ax.annotate("OOM", (c, 0.08), xycoords=ax.get_xaxis_transform(), xytext=(-6, 4),
                                    textcoords="offset points", ha="right", fontsize=7)
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_xticks(CONCS)
            ax.set_xticklabels([str(c) for c in CONCS])
            ax.yaxis.set_major_locator(ticker.LogLocator(base=10, subs=(1.0, 2.0, 5.0), numticks=14))
            ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", " ")))
            ax.yaxis.set_minor_locator(ticker.NullLocator())
            ax.set_title(f"{q} · perfil {L}", fontsize=9)
            ax.grid(True, which="major", color="0.88", lw=0.6)
            if j == 0:
                ax.set_ylabel("Latencia P99 (ms)")
            if i == 1:
                ax.set_xlabel("Concurrencia ($c$)")
    handles = [Line2D([0], [0], color=STY[e]["color"], ls=STY[e]["ls"], marker=STY[e]["marker"], ms=5.5, label=ENG_LABEL[e])
               for e in ENGINES]
    handles += [Line2D([0], [0], color="0.3", marker="o", mfc="white", ls="none", ms=5.5, label=r"$N<100$ (P99 poco fiable)"),
                Line2D([0], [0], color="0.3", marker="X", ls="none", ms=7, label="OOM (sin P99)")]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False, bbox_to_anchor=(0.5, -0.04),
               columnspacing=1.2, handletextpad=0.5, fontsize=7.5)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    save(fig, "fig_p99")
    plt.close(fig)


def fig_throttle(A):
    fig, axes = plt.subplots(1, 2, figsize=(6.5, 3.1))
    for ax, q in zip(axes, QUERIES):
        for e in ENGINES:
            for L in PROFILES:
                for c in CONCS:
                    a = A.get((e, L, q, False, c))
                    if not a or a["thr"] is None or a["qps"] is None:
                        continue
                    s = STY[e]
                    ax.plot([a["thr"]], [a["qps"]], marker=s["marker"], ms=6.5, mfc=PROF_FILL[L], mec=s["color"] if L != "C" else "black",
                            mew=1.0, ls="none")
                    ax.annotate(str(c), (a["thr"], a["qps"]), xytext=(4, 3), textcoords="offset points", fontsize=6.5, color="0.25")
        ax.set_xlabel("Tiempo CFS limitado (s, ventana de 300 s)")
        ax.set_title(q, fontsize=9)
        ax.grid(True, color="0.88", lw=0.6)
        ax.set_xlim(-30, 950)
        ax.set_ylim(bottom=-0.05 * ax.get_ylim()[1])
    axes[0].set_ylabel("Rendimiento mediano (QPS)")
    h = [Line2D([0], [0], marker=STY[e]["marker"], color="0.3", mfc="white", ls="none", ms=6, label=ENG_LABEL[e]) for e in ENGINES]
    h += [Line2D([0], [0], marker="o", color="0.3", mfc=PROF_FILL[L], ls="none", ms=6, label=f"Perfil {L}") for L in PROFILES]
    fig.legend(handles=h, loc="lower center", ncol=5, frameon=False, bbox_to_anchor=(0.5, -0.05), fontsize=7.5, columnspacing=1.0)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, "fig_throttle")
    plt.close(fig)


def read_series(rid):
    rows = []
    with open(RES / "raw" / rid / "series.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({k: float(v) for k, v in r.items() if v not in ("", None)})
    t0 = rows[0]["t"]
    return t0, rows


def fig_memory():
    runs = [("mongodb-C-Q1-c1-r1", "(a) MongoDB, Q1, $c=1$"),
            ("postgresql-C-Q1-c1-r1", "(b) PostgreSQL, Q1, $c=1$"),
            ("postgresql-C-Q1-c50-r1", "(c) PostgreSQL, Q1, $c=50$ (OOM)")]
    fig, axes = plt.subplots(1, 3, figsize=(6.5, 2.7), sharey=True)
    MIB = 1024 * 1024
    for ax, (rid, title) in zip(axes, runs):
        t0, rows = read_series(rid)
        t = [r["t"] - t0 for r in rows]
        eng = [(r["anon"] + r.get("shmem", 0)) / MIB for r in rows]
        fil = [r["file"] / MIB for r in rows]
        cur = [r["current"] / MIB for r in rows]
        ax.plot(t, cur, color="0.7", lw=1.0, label="memory.current")
        ax.plot(t, fil, color="0.4", lw=0.8, ls=":", label="file (caché de páginas)")
        ax.plot(t, eng, color="black", lw=1.2, label="anon + shmem (motor)")
        ax.axhline(1024, color="black", lw=0.8, ls="--")
        ax.axvline(60, color="0.5", lw=0.6, ls="-.")
        ax.axvline(360, color="0.5", lw=0.6, ls="-.")
        ax.set_title(title, fontsize=8)
        ax.set_xlabel("Tiempo (s)")
        ax.grid(True, color="0.9", lw=0.5)
        ax.set_xlim(0, 430)
        ax.set_ylim(0, 1150)
    axes[0].set_ylabel("Memoria del cgroup (MiB)")
    axes[0].text(8, 1040, "límite 1 024 MiB", fontsize=6.5, va="bottom")
    h, lbl = axes[2].get_legend_handles_labels()
    h.append(Line2D([0], [0], color="black", lw=0.8, ls="--"))
    lbl.append("límite del perfil C")
    h.append(Line2D([0], [0], color="0.5", lw=0.6, ls="-."))
    lbl.append("inicio y fin de la ventana")
    fig.legend(h, lbl, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, -0.1), fontsize=7, columnspacing=1.2)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    save(fig, "fig_memory")
    plt.close(fig)


def fig_m3(A):
    fig, axes = plt.subplots(1, 2, figsize=(6.5, 3.2), sharey=False)
    labels, groups = [], []
    for q in QUERIES:
        for L in PROFILES:
            groups.append((q, L))
    width = 0.26
    for ax, (key, ttl) in zip(axes, (("cpu1k", "(a) CPU·s por 1 000 documentos"), ("memk", "(b) MB·s por 1 000 documentos"))):
        x = 0
        ticks, tl = [], []
        for gi, (q, L) in enumerate(groups):
            for k, c in enumerate(CONCS):
                m, p = A.get(("mongodb", L, q, False, c)), A.get(("postgresql", L, q, False, c))
                xi = x + k * width
                if m and p and m[key] and p[key]:
                    ax.bar(xi, p[key] / m[key] - 1.0, bottom=1.0, width=width * 0.88, color=PROF_FILL[L], edgecolor="black", lw=0.7)
                else:
                    ax.plot([xi], [1.0], marker="x", color="black", ms=4)
            ticks.append(x + width)
            tl.append(f"{q}\n{L}")
            x += 1.2
        ax.axhline(1.0, color="black", lw=0.8)
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(ticker.LogLocator(base=10, subs=(1.0, 2.0, 5.0), numticks=14))
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ax.yaxis.set_minor_locator(ticker.NullLocator())
        ax.set_xticks(ticks)
        ax.set_xticklabels(tl, fontsize=7)
        ax.set_title(ttl, fontsize=8.5)
        ax.grid(True, axis="y", color="0.9", lw=0.5)
        ax.set_ylabel("Razón PostgreSQL / MongoDB")
    h = [matplotlib.patches.Patch(facecolor=PROF_FILL[L], edgecolor="black", label=f"Perfil {L}") for L in PROFILES]
    h.append(Line2D([0], [0], marker="x", color="black", ls="none", ms=4, label="sin dato (OOM)"))
    fig.legend(handles=h, loc="lower center", ncol=4, frameon=False, bbox_to_anchor=(0.5, -0.04), fontsize=7.5)
    fig.text(0.5, 0.0, "", fontsize=1)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, "fig_m3")
    plt.close(fig)


def fig_relaxed(A):
    fig, ax = plt.subplots(1, 1, figsize=(6.5, 2.9))
    xs, ticks, tl = 0, [], []
    ratios = [1.0]
    for L in PROFILES:
        for c in CONCS:
            for e in ENGINES:
                s, rx = A.get((e, L, "Q2", False, c)), A.get((e, L, "Q2", True, c))
                if s and rx and s["p99"] and rx["p99"]:
                    st_ = STY[e]
                    xo = -0.12 if e == "mongodb" else 0.12
                    ax.plot([xs + xo], [rx["p99"] / s["p99"]], marker=st_["marker"], color=st_["color"], ms=6, ls="none")
                    ratios.append(rx["p99"] / s["p99"])
            ticks.append(xs)
            tl.append(f"{L}\n{c}")
            xs += 1
        xs += 0.5
    ax.axhline(1.0, color="black", lw=0.8)
    ax.set_xticks(ticks)
    ax.set_xticklabels(tl, fontsize=7.5)
    ax.set_ylabel("P99 relajada / P99 estricta")
    ax.set_xlabel("Perfil (arriba) y concurrencia $c$ (abajo)")
    ax.grid(True, axis="y", color="0.9", lw=0.5)
    ax.set_ylim(min(ratios) * 0.9, max(ratios) * 1.18)
    h = [Line2D([0], [0], marker=STY[e]["marker"], color=STY[e]["color"], ls="none", ms=6, label=ENG_LABEL[e]) for e in ENGINES]
    ax.legend(handles=h, loc="upper left", frameon=False, ncol=2)
    fig.tight_layout()
    save(fig, "fig_relaxed")
    plt.close(fig)


# ----------------------------------------------------------------------------- hechos / macros
def journal_facts():
    ev = [json.loads(l) for l in (ROOT / "state" / "journal.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    start = next(e["ts"] for e in ev if e["event"] == "campaign_started")
    done = next((e["ts"] for e in reversed(ev) if e["event"] == "done"), ev[-1]["ts"])
    return dict(
        discarded=sum(e["event"] == "run_discarded" for e in ev),
        incidents=sum(e["event"] == "incident_opened" for e in ev),
        finished=sum(e["event"] == "run_finished" for e in ev),
        hours=(done - start) / 3600.0,
        halts=sum(e["event"] == "halted" for e in ev),
        resumes=sum(e["event"] == "resumed" for e in ev),
    )


def build_facts(A, runs):
    F = {}
    for q in QUERIES:
        for L in PROFILES:
            for c in CONCS:
                m, p = A.get(("mongodb", L, q, False, c)), A.get(("postgresql", L, q, False, c))
                key = f"{q}{L}c{c}"
                if m:
                    F[key + "mqps"] = f"{m['qps']:.2f}" if m["qps"] is not None else "---"
                    F[key + "mp99"] = thou(m["p99"]) if m["p99"] else "---"
                if p:
                    F[key + "pqps"] = f"{p['qps']:.2f}" if p["qps"] is not None else "---"
                    F[key + "pp99"] = thou(p["p99"]) if p["p99"] else "---"
                if m and p and m["p99"] and p["p99"]:
                    F[key + "rp99"] = f"{p['p99'] / m['p99']:.1f}"
                if m and p and m["qps"] and p["qps"]:
                    F[key + "rqps"] = f"{m['qps'] / p['qps']:.1f}"
                    F[key + "rqpsinv"] = f"{p['qps'] / m['qps']:.1f}"
                if m and p and m["cpu1k"] and p["cpu1k"]:
                    F[key + "rcpu"] = f"{p['cpu1k'] / m['cpu1k']:.1f}"
                if m and p and m["memk"] and p["memk"]:
                    F[key + "rmem"] = f"{p['memk'] / m['memk']:.1f}"
                if m:
                    F[key + "mthr"] = f"{m['thr']:.0f}" if m["thr"] is not None else "---"
                if p:
                    F[key + "pthr"] = f"{p['thr']:.0f}" if p["thr"] is not None else "---"
    # variaciones M1 (B, C frente a A) y razón relajada/estricta del P99
    for q in QUERIES:
        for e in ENGINES:
            for c in CONCS:
                base = A.get((e, "A", q, False, c))
                for L in ("B", "C"):
                    a = A.get((e, L, q, False, c))
                    if base and a and base["qps"]:
                        F[f"dq{q}{e[0]}{L}c{c}"] = f"{abs((a['qps'] - base['qps']) / base['qps'] * 100):.0f}"
                    if base and a and base["p99"] and a["p99"]:
                        F[f"dp{q}{e[0]}{L}c{c}"] = f"{(a['p99'] - base['p99']) / base['p99'] * 100:+.0f}".replace("+", "")
                for L in PROFILES:
                    sx, rx = A.get((e, L, q, False, c)), A.get((e, L, q, True, c))
                    if sx and rx and sx["p99"] and rx["p99"]:
                        F[f"rel{q}{e[0]}{L}c{c}"] = f"{rx['p99'] / sx['p99']:.2f}"
                        F[f"relq{q}{e[0]}{L}c{c}"] = f"{rx['qps'] / sx['qps']:.2f}" if sx["qps"] else "---"
    rr = [rx["p99"] / sx["p99"] for (e, L, q, rl, c), rx in A.items() if q == "Q2" and rl
          for sx in [A.get((e, L, q, False, c))] if sx and sx["p99"] and rx["p99"]]
    F["relq2min"], F["relq2max"] = f"{min(rr):.2f}", f"{max(rr):.2f}"
    jf = journal_facts()
    F.update({k: (f"{v:.1f}" if isinstance(v, float) else str(v)) for k, v in jf.items()})
    tot = defaultdict(int)
    for r in runs:
        tot[(r["relaxed"], r["status"])] += 1
    for rel, nm in ((False, "main"), (True, "annex")):
        for s_ in ("ok", "spill", "oom"):
            F[f"{nm}{s_}"] = str(tot[(rel, s_)])
        F[f"{nm}total"] = str(sum(tot[(rel, s_)] for s_ in ("ok", "spill", "oom")))
    oomk = sum((r["oomk"] or 0) for r in runs)
    F["oomkills"] = str(oomk)
    ra = [r["rss"] for r in runs if r["engine"] == "postgresql" and r["status"] == "oom"]
    F["oomrssmax"] = f"{max(ra):.0f}" if ra else "---"
    return F


def main():
    runs = load()
    A = cell_agg(runs)
    write(TAB / "tab_q1.tex", table_results(A, "Q1"))
    write(TAB / "tab_q2.tex", table_results(A, "Q2"))
    write(TAB / "tab_relaxed.tex", table_relaxed(A))
    write(TAB / "tab_status.tex", table_status(runs))
    write(TAB / "tab_m1.tex", table_m1(A))
    write(TAB / "tab_m3.tex", table_m3(A))
    plans, pf = table_plans()
    write(TAB / "tab_plans.tex", plans)
    write(TAB / "plan_listing.txt", plan_listing())
    fig_p99(A)
    fig_throttle(A)
    fig_memory()
    fig_m3(A)
    fig_relaxed(A)
    F = build_facts(A, runs)
    for (q, L), (pg, mg) in pf.items():
        F[f"plan{q}{L}pgms"] = fm(pg["ms"], 0)
        F[f"plan{q}{L}mgms"] = fm(mg["ms"], 0)
        F[f"plan{q}{L}pgdisk"] = fm(pg["disk_mb"], 0)
        F[f"plan{q}{L}mgdocs"] = fm(mg["docs"], 0)
        F[f"plan{q}{L}mgkeys"] = fm(mg["keys"], 0)
    pgq1 = pf[("Q1", "C")][0]
    F["pgq1estrows"] = fm(pgq1["est_rows"], 0)
    lines = ["\\newcommand{\\defFact}[2]{\\expandafter\\def\\csname fact@#1\\endcsname{#2}}",
             "\\newcommand{\\fact}[1]{\\csname fact@#1\\endcsname}"]
    lines += [f"\\defFact{{{k}}}{{{v}}}" for k, v in sorted(F.items())]
    write(OUT / "numbers.tex", "\n".join(lines) + "\n")
    write(OUT / "src" / "facts.txt", "\n".join(f"{k} = {v}" for k, v in sorted(F.items())) + "\n")
    print("OK:", len(runs), "corridas;", len(A), "celdas;", len(F), "cifras; fuente de figuras:", FONT)


if __name__ == "__main__":
    main()
