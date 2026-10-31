import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CSV_HEADER = [
    "timestamp_epoch", "engine", "profile", "query_id", "concurrency", "run_number",
    "throughput_qps", "latency_p50_ms", "latency_p95_ms", "latency_p99_ms",
    "cpu_throttled_seconds", "max_rss_mb", "disk_spill", "status",
]

ENGINES = ("mongodb", "postgresql")
PROFILES = {
    "A": "PROFILE_A_BASELINE",
    "B": "PROFILE_B_MEDIUM",
    "C": "PROFILE_C_SEVERE",
}
PROFILE_SPECS = {
    "A": {"cpus": 4.0, "memory_mb": 4096, "memswap_mb": 4096, "pg_shared_buffers": "1024MB", "mongo_wt_cache_gb": 1.5},
    "B": {"cpus": 2.0, "memory_mb": 2048, "memswap_mb": 2048, "pg_shared_buffers": "512MB", "mongo_wt_cache_gb": 0.5},
    "C": {"cpus": 1.0, "memory_mb": 1024, "memswap_mb": 1024, "pg_shared_buffers": "256MB", "mongo_wt_cache_gb": 0.25},
}
QUERIES = ("Q1", "Q2")
CONCURRENCIES = (1, 10, 50)
REPETITIONS = 3

# Ambas consultas (Q1 y Q2) fijan explícitamente los parámetros de memoria por sesión/operación:
# - strict: 36 celdas principales en mediciones.csv (4MB en Postgres, allowDiskUse=False en Mongo)
# - relaxed: celdas de contraste en anexo_variantes.csv (64MB en Postgres, allowDiskUse=True en Mongo),
#   en PROFILE_C_SEVERE x c={1,10,50}. Su query_id lleva el sufijo "_relaxed" para que la
#   concatenación de CSV del estudio posterior no colisione con las filas strict.
VARIANTS = {
    "strict": {"mongodb": {"allowDiskUse": False}, "postgresql": {"work_mem": "4MB"}},
    "relaxed": {"mongodb": {"allowDiskUse": True}, "postgresql": {"work_mem": "64MB"}},
}


def query_label(query, variant):
    """Valor de la columna query_id: Q1/Q2 en strict; Q1_relaxed/Q2_relaxed en relaxed."""
    return query if variant == "strict" else f"{query}_{variant}"


# Documentos base procesados por consulta para el cálculo normalizado de M3 (por cada 1 000 documentos)
DOCS_PER_QUERY = {
    "Q1": 500_000,
    "Q1_FLOAT8": 500_000,  # micro-comparativa ::float8 (solo PostgreSQL, vía run-one)
    "Q2": 500_000,
}

# P99 con menos de 100 muestras completadas se reporta como no confiable (resume summarize)
MIN_P99_SAMPLES = 100

DB_CPUS = {0, 1, 2, 3}
RUNNER_CPUS = {4, 5, 10, 11}
IDLE_SIBLING_CPUS = {6, 7, 8, 9}

MONGO_URI = os.environ.get("BENCH_MONGO_URI", "mongodb://127.0.0.1:27018/?directConnection=true")
MONGO_DB = "bench"
PG_DSN = os.environ.get("BENCH_PG_DSN", "host=127.0.0.1 port=5433 user=bench dbname=bench")
PROMETHEUS_URL = os.environ.get("BENCH_PROMETHEUS_URL", "http://127.0.0.1:9091")

CONTAINERS = {"mongodb": "bench-mongodb", "postgresql": "bench-postgresql"}

SCALE = float(os.environ.get("BENCH_PHASE_SCALE", "1"))
WARMUP_S = 60 * SCALE
MEASURE_S = 300 * SCALE
COOLDOWN_S = 60 * SCALE

# Timeout máximo por sentencia/operación en la sesión (cubre toda la ventana de medición + warmup)
SESSION_STATEMENT_TIMEOUT_MS = int(max(60_000, (WARMUP_S + MEASURE_S) * 1000))

WORKER_JOIN_GRACE_S = 25
MIN_SAMPLE_RATIO = 0.8
NOISE_BUSY_PCT = 30.0

STATE_DIR = ROOT / os.environ.get("BENCH_STATE_DIR", "state")
RESULTS_DIR = ROOT / os.environ.get("BENCH_RESULTS_DIR", "results")
DATA_PATH = ROOT / os.environ.get("BENCH_DATA", "data/orders.ndjson")
REHEARSAL = os.environ.get("BENCH_REHEARSAL") == "1"
