#!/usr/bin/env python3
"""Muestra las frases del .tex que usan \\fact{..} con los valores sustituidos (revisión de coherencia)."""
import re
from pathlib import Path

REP = Path(__file__).resolve().parents[1]
facts = dict(re.findall(r"\\defFact\{([^}]+)\}\{([^}]*)\}", (REP / "numbers.tex").read_text(encoding="utf-8")))
txt = (REP / "reporte_tecnico.tex").read_text(encoding="utf-8")
paras = re.split(r"\n\s*\n", txt)
for p in paras:
    if "\\fact{" not in p:
        continue
    q = re.sub(r"\\fact\{([^}]+)\}", lambda m: "<" + facts.get(m.group(1), "??" + m.group(1)) + ">", p)
    q = q.replace("\\,", " ").replace("\\%", "%").replace("\n", " ")
    print("-", q[:1400], "\n")
