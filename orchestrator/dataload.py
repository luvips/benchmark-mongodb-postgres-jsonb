"""Valida el NDJSON contra el contrato, lo carga en ambos motores y verifica sumas de control."""
import json
import re
import uuid

try:
    import psycopg
    import pymongo
except ImportError:
    psycopg = None
    pymongo = None

from benchmark_runner.config import MONGO_DB, MONGO_URI, PG_DSN, ROOT

TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
BATCH = 5000


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate(path):
    errors, seen = [], set()
    count = size = items = completed = before = over_50 = prices = 0

    def err(n, msg):
        if len(errors) < 20:
            errors.append(f"línea {n}: {msg}")

    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            size += len(line.encode("utf-8"))
            try:
                doc = json.loads(line)
                oid = doc["order_id"]
                if str(uuid.UUID(oid)) != oid:
                    err(n, "order_id no es UUID en minúsculas")
                if oid in seen:
                    err(n, "order_id duplicado")
                seen.add(oid)
                ts = doc["timestamp"]
                if not TIMESTAMP.match(ts):
                    err(n, f"timestamp inválido: {ts}")
                before += ts < "2026-01-01T00:00:00Z"
                if not _is_number(doc["payment"]["final_total"]):
                    err(n, "payment.final_total no es numérico")
                completed += doc["payment"]["status"] == "completed"
                doc["customer"]["geo"]["country"]
                for item in doc["items"]:
                    items += 1
                    item["category"]
                    if not (_is_number(item["qty"]) and _is_number(item["price"])):
                        err(n, "items.qty o items.price no es numérico")
                    prices += 1
                    over_50 += item["price"] > 50
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                err(n, f"{type(exc).__name__}: {exc}")
            count = n
    if count and not 0 < before < count:
        errors.append("las fechas no cruzan 2026-01-01")
    if count and not 0 < completed < count:
        errors.append("payment.status no mezcla completed con otros valores")
    if prices and not 0 < over_50 < prices:
        errors.append("items.price no mezcla valores sobre y bajo 50")
    return {
        "count": count,
        "avg_doc_bytes": size / max(1, count),
        "total_mb": size / 2**20,
        "avg_items": items / max(1, count),
        "completed_ratio": completed / max(1, count),
        "before_2026_ratio": before / max(1, count),
        "over_50_ratio": over_50 / max(1, prices),
        "errors": errors,
    }


def _batches(path):
    batch = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            batch.append(line)
            if len(batch) == BATCH:
                yield batch
                batch = []
    if batch:
        yield batch


def load_mongo(path):
    with pymongo.MongoClient(MONGO_URI) as client:
        db = client[MONGO_DB]
        db.drop_collection("orders")
        for batch in _batches(path):
            db.orders.insert_many([json.loads(l) for l in batch], ordered=False)
        db.orders.create_index("timestamp")
        db.orders.create_index("order_id")
        result = next(db.orders.aggregate([{"$group": {
            "_id": None,
            "count": {"$sum": 1},
            "total": {"$sum": "$payment.final_total"},
            "completed": {"$sum": {"$cond": [{"$eq": ["$payment.status", "completed"]}, 1, 0]}},
        }}]))
    return {k: result[k] for k in ("count", "total", "completed")}


def load_pg(path):
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS orders")
        conn.execute((ROOT / "queries" / "setup.pg.sql").read_text(encoding="utf-8"))
        with conn.cursor() as cur, cur.copy("COPY orders (id, data) FROM STDIN") as copy:
            for batch in _batches(path):
                for line in batch:
                    copy.write_row((json.loads(line)["order_id"], line.strip()))
        conn.execute("VACUUM (FREEZE, ANALYZE) orders")
        row = conn.execute(
            "SELECT count(*), sum((data->'payment'->>'final_total')::numeric),"
            " count(*) FILTER (WHERE data->'payment'->>'status' = 'completed') FROM orders"
        ).fetchone()
    return {"count": row[0], "total": float(row[1]), "completed": row[2]}


def same_dataset(mongo, pg):
    return (
        mongo["count"] == pg["count"]
        and mongo["completed"] == pg["completed"]
        and abs(mongo["total"] - pg["total"]) <= 1e-6 * max(1.0, abs(pg["total"]))
    )
