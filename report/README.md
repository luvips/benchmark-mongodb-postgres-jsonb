# Reporte técnico (LaTeX, APA 7)

Contenido de esta carpeta:

| Ruta | Qué es |
| :--- | :--- |
| `reporte_tecnico.tex` | Documento principal (portada, resumen, método, resultados, discusión, referencias y 4 apéndices). |
| `numbers.tex` | Cifras citadas en el texto (`\fact{clave}`), **generado** desde `results/`. |
| `figures/*.pdf` | Figuras vectoriales (APA: sin título interno, legibles en escala de grises). **Generadas**. |
| `tables/*.tex` | Tablas con `booktabs` y el fragmento del plan de ejecución. **Generadas**. |
| `src/build_assets.py` | Genera todo lo anterior a partir de los resultados reales (solo lee `results/`). |
| `src/lint_tex.py` | Verificación estática del `.tex` (no requiere TeX instalado). |
| `src/expand_facts.py` | Muestra las frases con cifras ya sustituidas, para revisar coherencia. |
| `src/preview/*.png` | Vistas previas de las figuras (no las usa LaTeX). |

## Compilar

Con `pdflatex` (no requiere `biber`; la lista de referencias es manual), **desde esta carpeta** y dos veces:

```
pdflatex reporte_tecnico.tex
pdflatex reporte_tecnico.tex
```

En Overleaf: subir la carpeta `report/` (sin `src/`) y compilar con pdfLaTeX.

## Regenerar figuras, tablas y cifras

Desde la raíz del repositorio:

```
./.venv/Scripts/python.exe report/src/build_assets.py
./.venv/Scripts/python.exe report/src/lint_tex.py
```

## Antes de entregar

1. Editar en el preámbulo de `reporte_tecnico.tex` los datos de portada: `\autor`, `\docente`, `\fechaentrega`.
2. Verificar las referencias (autores, años y DOI) contra la fuente: se redactaron sin acceso a internet.
3. Revisar el Apéndice C: menciona que la supervisión operativa se apoyó en un asistente automatizado; quitar esa frase si no se desea declarar.
