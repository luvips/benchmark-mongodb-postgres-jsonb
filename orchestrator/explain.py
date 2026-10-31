"""Planes de ejecución fuera de la ventana medida, para la sección 5 del reporte."""
import json

try:
    import psycopg
    import pymongo
except ImportError:
    psycopg = None
    pymongo = None

from benchmark_runner.config import MONGO_DB, MONGO_URI, PG_DSN
from benchmark_runner.workers import load_query, variant_options


def explain(engine, query, variant):
    options = variant_options(engine, query, variant)
    if engine == "mongodb":
        with pymongo.MongoClient(MONGO_URI) as client:
            plan = client[MONGO_DB].command(
                "explain",
                {"aggregate": "orders", "pipeline": load_query(engine, query), "cursor": {}, **options},
                verbosity="executionStats",
            )
        return json.dumps(plan, indent=1, default=str)
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        if "work_mem" in options:
            conn.execute(f"SET work_mem = '{options['work_mem']}'")
        rows = conn.execute("EXPLAIN (ANALYZE, BUFFERS, VERBOSE) " + load_query(engine, query)).fetchall()
    return "\n".join(r[0] for r in rows)
