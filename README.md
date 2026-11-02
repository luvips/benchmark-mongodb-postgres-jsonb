# Medición de métricas en bases documentales

Contención de CPU y memoria en agregaciones JSON: **MongoDB 9.0.4 frente a PostgreSQL 18.6 (JSONB)** bajo límites de Docker cgroups v2.

| | |
|---|---|
| **Alumna** | Luvia Magali Hidalgo Garcia |
| **Matrícula** | 243732 |
| **Materia** | Bases de Datos en la Nube |
| **Docente** | Horacio Irán Solís Cisneros |
| **Institución** | Universidad Politécnica de Chiapas — IT2iD |

## Entregables

| Entregable (playbook, sección 8) | Ubicación |
|---|---|
| Reporte técnico (7 secciones) | `docs/reporte_latex/reporte_benchmark.pdf` (fuente `.tex` en la misma carpeta) |
| CSV de mediciones | `results/mediciones.csv` (108 corridas) y `results/anexo_variantes.csv` (72 corridas) |
| Evidencias de spill/OOM | `results/raw/<corrida>/spill_evidence.log`, `oom_evidence.log` y `meta.json` |
| Planes de ejecución | `results/explain/` |
| `docker-compose.yml` parametrizado | `docker-compose.yml` y `profiles/{A,B,C}.env` |
| Generador del dataset (500k) | `synthetic_data_generator.py` |
| Runner multiproceso | `benchmark_runner/` |
| Consultas | `queries/` |
| Automatización de la matriz | `run_benchmarks.sh` y `orchestrator/` |

## Requisitos

- Docker con cgroups v2 y Docker Compose v2.
- Python 3.10 o superior: `pip install -e .` (instala `psycopg` y `pymongo`).

## Ejecución

```bash
python synthetic_data_generator.py --n 500000 --seed 42 --out-dir data
docker compose pull
./run_benchmarks.sh doctor        # verificaciones previas
./run_benchmarks.sh load-data     # carga y verificación de paridad entre motores
./run_benchmarks.sh start         # campaña de 180 corridas
./run_benchmarks.sh status        # progreso
./run_benchmarks.sh summarize     # mediana y rango por celda
```

Para regenerar las tablas y figuras del reporte: `python docs/reporte_latex/generar_tablas_figuras.py`.

## Matriz experimental

- **Principal:** 2 motores × 3 perfiles × 2 consultas × 3 concurrencias = 36 celdas × 3 repeticiones = 108 corridas.
- **Anexo `relaxed`:** 72 corridas más (`work_mem = 64MB` / `allowDiskUse: true`).
- **Ciclo por corrida:** recreación del contenedor, `drop_caches`, 60 s de warmup, 300 s de medición y 60 s de cooldown.

## Entorno

La campaña se ejecutó con Docker Desktop 4.55.0 (Docker Engine 29.1.3) sobre el kernel Linux 6.6 de WSL2, en un equipo Windows con Intel Core i7-1355U. Sus implicaciones están en la sección 6.6 del reporte.
