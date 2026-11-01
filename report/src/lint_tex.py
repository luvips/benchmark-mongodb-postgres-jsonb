#!/usr/bin/env python3
"""
Verificación estática de report/reporte_tecnico.tex (sin necesidad de TeX instalado).

Comprueba: entornos \\begin/\\end balanceados, llaves balanceadas, cifras \\fact{..} definidas en numbers.tex,
\\ref/\\label consistentes, archivos de \\input/\\includegraphics existentes, caracteres Unicode que pdflatex
no soporta sin configuración extra, y que cada fila de las tablas generadas tenga el número de columnas correcto.

Uso:  ./.venv/Scripts/python.exe report/src/lint_tex.py
"""
import re
import sys
from collections import Counter
from pathlib import Path

REP = Path(__file__).resolve().parents[1]
errors, warns = [], []


def strip_comments(t):
    out = []
    for ln in t.splitlines():
        i = 0
        while i < len(ln):
            if ln[i] == "%" and (i == 0 or ln[i - 1] != "\\"):
                ln = ln[:i]
                break
            i += 1
        out.append(ln)
    return "\n".join(out)


def brace_balance(t, name):
    t2 = re.sub(r"\\[\\{}%&_#$^~]", "", t)  # quita escapes \\ \{ \} \% ...
    depth = 0
    for i, ch in enumerate(t2):
        depth += ch == "{"
        depth -= ch == "}"
        if depth < 0:
            errors.append(f"{name}: llave de cierre sobrante cerca de: {t2[max(0, i - 40):i + 10]!r}")
            return
    if depth:
        errors.append(f"{name}: {depth} llave(s) sin cerrar")


main = (REP / "reporte_tecnico.tex").read_text(encoding="utf-8")
body = strip_comments(main)
brace_balance(body, "reporte_tecnico.tex")

# entornos
begins = Counter(re.findall(r"\\begin\{([^}]+)\}", body))
ends = Counter(re.findall(r"\\end\{([^}]+)\}", body))
for env in set(begins) | set(ends):
    if begins[env] != ends[env]:
        errors.append(f"entorno '{env}': {begins[env]} begin vs {ends[env]} end")

# cifras
numbers = (REP / "numbers.tex").read_text(encoding="utf-8")
defined = set(re.findall(r"\\defFact\{([^}]+)\}", numbers))
used = re.findall(r"\\fact\{([^}]+)\}", body)
for k in sorted(set(used)):
    if k not in defined:
        errors.append(f"\\fact{{{k}}} no está definida en numbers.tex")
dash = [k for k in set(used) if k in defined and re.search(rf"\\defFact\{{{re.escape(k)}\}}\{{---\}}", numbers)]
for k in dash:
    warns.append(f"\\fact{{{k}}} vale '---' (sin dato)")

# referencias
labels = re.findall(r"\\label\{([^}]+)\}", body)
for _t in sorted((REP / "tables").glob("tab_*.tex")):  # etiquetas definidas dentro de tablas generadas
    labels += re.findall(r"\\label\{([^}]+)\}", _t.read_text(encoding="utf-8"))
for l, n in Counter(labels).items():
    if n > 1:
        errors.append(f"etiqueta duplicada: {l}")
for r in sorted(set(re.findall(r"\\ref\{([^}]+)\}", body))):
    if r not in labels:
        errors.append(f"\\ref{{{r}}} sin \\label")
for l in labels:
    if not re.search(rf"\\ref\{{{re.escape(l)}\}}", body):
        warns.append(f"etiqueta '{l}' nunca referenciada")

# archivos
for f in re.findall(r"\\input\{([^}]+)\}", body):
    if f.startswith("tables/#"):
        continue
    if not (REP / (f if f.endswith(".tex") else f + ".tex")).exists():
        errors.append(f"\\input{{{f}}}: archivo no existe")
for f in re.findall(r"\\tablefile\{([^}]+)\}", body):
    if not (REP / "tables" / (f + ".tex")).exists():
        errors.append(f"\\tablefile{{{f}}}: archivo no existe")
for f in re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", body):
    if not (REP / f).exists():
        errors.append(f"\\includegraphics{{{f}}}: archivo no existe")
for f in re.findall(r"\\verbatiminput\{([^}]+)\}", body):
    if not (REP / f).exists():
        errors.append(f"\\verbatiminput{{{f}}}: archivo no existe")

# Unicode problemático (pdflatex + inputenc utf8 + T1 admite acentos españoles, comillas «» y guiones)
OK = set("áéíóúüñÁÉÍÓÚÜÑ¿¡«»–—·ªº")
for path in [REP / "reporte_tecnico.tex", REP / "numbers.tex", *sorted((REP / "tables").glob("*"))]:
    txt = path.read_text(encoding="utf-8")
    bad = Counter(ch for ch in txt if ord(ch) > 127 and ch not in OK)
    for ch, n in bad.items():
        errors.append(f"{path.name}: carácter no soportado {ch!r} (U+{ord(ch):04X}) x{n}")

# columnas por fila en tablas generadas (tabular de una sola especificación)
for path in sorted((REP / "tables").glob("tab_*.tex")):
    txt = path.read_text(encoding="utf-8")
    m = re.search(r"\\begin\{(?:tabular|longtable)\}\{", txt)
    if not m:
        continue
    depth, i0, i = 1, m.end(), m.end()
    while depth and i < len(txt):
        depth += txt[i] == "{"
        depth -= txt[i] == "}"
        i += 1
    spec = txt[i0:i - 1]
    spec = re.sub(r"@\{[^}]*\}", "", spec)
    spec = re.sub(r"[pmb]\{[^}]*\}", "c", spec)
    ncol = len(re.findall(r"[lcrp]", spec))
    for ln in txt.splitlines():
        if ln.rstrip().endswith("\\\\") and not ln.lstrip().startswith("\\"):
            cells = len(re.split(r"(?<!\\)&", ln.rstrip().rstrip("\\").rstrip()))
            if "multicolumn" in ln:
                continue
            if cells != ncol:
                errors.append(f"{path.name}: fila con {cells} columnas, se esperaban {ncol}: {ln[:70]}")
                break

print(f"cifras usadas: {len(set(used))}; etiquetas: {len(labels)}; entornos: {sum(begins.values())}")
for w in warns:
    print("AVISO :", w)
for e in errors:
    print("ERROR :", e)
print("RESULTADO:", "FALLA" if errors else "OK (sin errores estáticos)")
sys.exit(1 if errors else 0)
