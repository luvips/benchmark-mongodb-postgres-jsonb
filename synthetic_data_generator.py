#!/usr/bin/env python3
r"""
synthetic_data_generator.py
===========================
Generador determinista del dataset sintético de órdenes (500 000 documentos
JSON, 3 niveles de anidamiento, promedio ~3 ítems por orden) para el benchmark
"Medición de métricas en bases documentales: MongoDB vs PostgreSQL JSONB".

Salidas (en --out-dir):
  orders.ndjson          Un documento JSON por línea     -> carga en MongoDB / PostgreSQL
  orders_pg.csv          Columnas (id, data) opcional    -> \copy directo en PostgreSQL
  dataset_manifest.json  Semilla, parámetros, distribuciones, cardinalidades y SHA-256
                         del flujo JSON canónico.

Especificación de distribuciones y reconciliación con el Playbook:
------------------------------------------------------------------
1. Semilla fija por defecto: seed = 42 (reproducibilidad byte a byte).
2. Cardinalidades de agrupación para Q1:
   - 8 países (`country`: MX, US, CO, AR, ES, BR, CL, PE)
   - 12 categorías (`category`: electronics, home, fashion, sports, beauty, toys,
     books, grocery, automotive, garden, office, pet_supplies)
   - Total de grupos posibles en Q1: 8 x 12 = 96 grupos.
3. Distribución de precios (`items[].price`):
   - Log-normal parametrizada por categoría (mu entre 2.80 y 4.70, sigma entre 0.40 y 0.80).
   - Selectividad resultante para `items.price > 50` en Q1: ~42.97 % de los ~1.5 M de ítems.
4. Distribución de `timestamp` y `payment.status` para Q2:
   - `timestamp`: uniforme en UTC dentro de [2025-01-01T00:00:00Z, 2026-10-30T23:59:59Z]
     en formato exacto ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`).
   - Corte `timestamp >= 2026-01-01T00:00:00Z`: ~45.34 % de los documentos.
   - `payment.status == 'completed'`: ~79.94 % de los documentos.
   - Selectividad conjunta de Q2 (`completed` AND `>= 2026-01-01`): ~36.26 % (~181 300 órdenes).
5. Reconciliación `payment.totals` vs `payment.final_total`:
   - Incluye tanto el objeto anidado `payment.totals` (`subtotal`, `discount_total`,
     `tax`, `shipping`, nivel 3) como el atributo numérico de primer nivel
     `payment.final_total` requerido por el `ORDER BY` / `$sort` de Q2.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
Q2_CUTOFF = "2026-01-01T00:00:00Z"
Q1_PRICE_THRESHOLD = 50

GEO = {
    "MX": (0.28, 16.0, [
        ("Chiapas", "Tuxtla Gutierrez", 16.75, -93.12, "29"),
        ("Jalisco", "Guadalajara", 20.67, -103.35, "44"),
        ("Nuevo Leon", "Monterrey", 25.67, -100.31, "64"),
        ("Ciudad de Mexico", "Ciudad de Mexico", 19.43, -99.13, "06"),
        ("Yucatan", "Merida", 20.97, -89.62, "97"),
    ]),
    "US": (0.20, 8.0, [
        ("California", "Los Angeles", 34.05, -118.24, "90"),
        ("Texas", "Austin", 30.27, -97.74, "78"),
        ("New York", "New York", 40.71, -74.01, "10"),
        ("Florida", "Miami", 25.76, -80.19, "33"),
    ]),
    "CO": (0.10, 19.0, [
        ("Cundinamarca", "Bogota", 4.71, -74.07, "11"),
        ("Antioquia", "Medellin", 6.24, -75.58, "05"),
    ]),
    "AR": (0.08, 21.0, [
        ("Buenos Aires", "Buenos Aires", -34.60, -58.38, "10"),
        ("Cordoba", "Cordoba", -31.42, -64.18, "50"),
    ]),
    "ES": (0.10, 21.0, [
        ("Madrid", "Madrid", 40.42, -3.70, "28"),
        ("Cataluna", "Barcelona", 41.39, 2.17, "08"),
    ]),
    "BR": (0.10, 17.0, [
        ("Sao Paulo", "Sao Paulo", -23.55, -46.63, "01"),
        ("Rio de Janeiro", "Rio de Janeiro", -22.91, -43.17, "20"),
    ]),
    "CL": (0.07, 19.0, [
        ("Metropolitana", "Santiago", -33.45, -70.67, "83"),
    ]),
    "PE": (0.07, 18.0, [
        ("Lima", "Lima", -12.05, -77.04, "15"),
    ]),
}

CATEGORIES = {
    "electronics":  (0.14, 4.70, 0.80, ["Samsung", "Sony", "LG", "Xiaomi", "Lenovo", "Logitech"]),
    "home":         (0.12, 4.00, 0.70, ["Ikea", "Oster", "Philips", "Mabe", "Whirlpool"]),
    "fashion":      (0.14, 3.90, 0.60, ["Nike", "Adidas", "Zara", "Levis", "Puma"]),
    "sports":       (0.08, 4.00, 0.60, ["Decathlon", "Wilson", "Reebok", "Under Armour"]),
    "beauty":       (0.08, 3.50, 0.50, ["Loreal", "Nivea", "Maybelline", "Dove"]),
    "toys":         (0.07, 3.60, 0.60, ["Lego", "Mattel", "Hasbro", "Fisher-Price"]),
    "books":        (0.07, 3.00, 0.40, ["Penguin", "Planeta", "Alfaguara", "Oxford"]),
    "grocery":      (0.10, 2.80, 0.50, ["Nestle", "Bimbo", "Danone", "Kelloggs"]),
    "automotive":   (0.06, 4.50, 0.70, ["Bosch", "Michelin", "Castrol", "3M"]),
    "garden":       (0.05, 3.80, 0.60, ["Truper", "Black+Decker", "Bosch", "Gardena"]),
    "office":       (0.05, 3.70, 0.60, ["HP", "Epson", "Canon", "Brother"]),
    "pet_supplies": (0.04, 3.40, 0.50, ["Purina", "Pedigree", "Royal Canin", "Whiskas"]),
}

TIERS = (["bronze", "silver", "gold", "platinum"], [0.50, 0.30, 0.15, 0.05])
SEGMENTS = (["consumer", "small_business", "enterprise"], [0.75, 0.20, 0.05])
STATUSES = (["completed", "pending", "failed", "refunded"], [0.80, 0.08, 0.07, 0.05])
METHODS = ["credit_card", "debit_card", "paypal", "bank_transfer", "cash_on_delivery", "wallet"]
CHANNELS = (["web", "mobile_app", "pos", "marketplace", "call_center"], [0.40, 0.35, 0.10, 0.10, 0.05])
SHIPPING = ([0.0, 4.99, 9.99, 19.99], [0.35, 0.30, 0.25, 0.10])
PROMO_CODES = ["WELCOME10", "SPRING15", "VIP20", "FLASH5", "FREESHIP", "BLACKFRI"]
ITEMS_PER_ORDER = ([1, 2, 3, 4, 5], [0.15, 0.22, 0.26, 0.22, 0.15])
QTY = ([1, 2, 3, 4, 5, 10], [0.50, 0.25, 0.12, 0.07, 0.04, 0.02])
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
    "okhttp/4.12.0",
    "PostmanRuntime/7.37.0",
]


def make_uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def build_customer_pool(rng: random.Random, size: int) -> list[dict]:
    countries = list(GEO)
    c_weights = [GEO[c][0] for c in countries]
    pool = []
    for i in range(size):
        country = rng.choices(countries, c_weights)[0]
        _, tax, places = GEO[country]
        state, city, lat, lon, zip_prefix = rng.choice(places)
        pool.append({
            "_tax": tax,
            "doc": {
                "id": f"CUST-{i + 1:07d}",
                "tier": rng.choices(*TIERS)[0],
                "segment": rng.choices(*SEGMENTS)[0],
                "geo": {
                    "country": country,
                    "state": state,
                    "city": city,
                    "zip": f"{zip_prefix}{rng.randint(0, 999):03d}",
                    "coordinates": [round(lon + rng.uniform(-0.3, 0.3), 5),
                                    round(lat + rng.uniform(-0.3, 0.3), 5)],
                },
            },
        })
    return pool


def build_item(rng: random.Random, cat_names, cat_weights, tax_pct: float) -> tuple[dict, float, float]:
    cat = rng.choices(cat_names, cat_weights)[0]
    _, mu, sigma, brands = CATEGORIES[cat]
    price = max(1.0, round(rng.lognormvariate(mu, sigma), 2))
    qty = rng.choices(*QTY)[0]
    gross = qty * price

    r = rng.random()
    if r < 0.75:
        discounts, disc = 0, 0.0
    elif r < 0.90:
        disc = round(gross * rng.choice((0.05, 0.10, 0.15, 0.20)), 2)
        discounts = disc
    else:
        discounts = [
            {"code": rng.choice(PROMO_CODES), "amount": round(gross * rng.uniform(0.03, 0.08), 2)}
            for _ in range(rng.randint(1, 2))
        ]
        disc = round(sum(d["amount"] for d in discounts), 2)

    item = {
        "sku": f"{cat[:3].upper()}-{rng.randint(100000, 999999)}",
        "category": cat,
        "brand": rng.choice(brands),
        "qty": qty,
        "price": price,
        "tax_pct": 0.0 if cat == "grocery" else tax_pct,
        "discounts": discounts,
    }
    return item, gross, disc


def build_order(rng: random.Random, customers: list[dict], ts_lo: int, ts_hi: int,
                cat_names: list[str], cat_weights: list[float]) -> dict:
    cust = customers[rng.randrange(len(customers))]
    tax_rate = cust["_tax"]

    items, subtotal, disc_total, tax_total = [], 0.0, 0.0, 0.0
    for _ in range(rng.choices(*ITEMS_PER_ORDER)[0]):
        item, gross, disc = build_item(rng, cat_names, cat_weights, tax_rate)
        items.append(item)
        subtotal += gross
        disc_total += disc
        tax_total += (gross - disc) * item["tax_pct"] / 100.0

    shipping = rng.choices(*SHIPPING)[0]
    final_total = round(subtotal - disc_total + tax_total + shipping, 2)
    ts = time.strftime(TS_FMT, time.gmtime(rng.randint(ts_lo, ts_hi)))

    return {
        "order_id": make_uuid(rng),
        "timestamp": ts,
        "customer": cust["doc"],
        "items": items,
        "payment": {
            "method": rng.choice(METHODS),
            "status": rng.choices(*STATUSES)[0],
            "transaction_id": f"TXN-{rng.getrandbits(64):016x}",
            "gateway_latency_ms": int(rng.lognormvariate(4.8, 0.5)),
            "totals": {
                "subtotal": round(subtotal, 2),
                "discount_total": round(disc_total, 2),
                "tax": round(tax_total, 2),
                "shipping": shipping,
            },
            "final_total": final_total,
        },
        "metadata": {
            "channel": rng.choices(*CHANNELS)[0],
            "user_agent": rng.choice(USER_AGENTS),
            "session_id": make_uuid(rng),
            "flags": {
                "is_gift": rng.random() < 0.08,
                "first_purchase": rng.random() < 0.12,
                "express_shipping": shipping >= 9.99,
                "fraud_review": rng.random() < 0.02,
            },
        },
    }


def parse_ts(value: str) -> int:
    return int(datetime.strptime(value, TS_FMT).replace(tzinfo=timezone.utc).timestamp())


def main() -> int:
    ap = argparse.ArgumentParser(description="Generador determinista del dataset sintético de 500k órdenes.")
    ap.add_argument("--n", type=int, default=500_000, help="número de documentos (default: 500000)")
    ap.add_argument("--seed", type=int, default=42, help="semilla del RNG (default: 42)")
    ap.add_argument("--out-dir", type=Path, default=Path("data"), help="directorio de salida")
    ap.add_argument("--format", choices=["ndjson", "csv", "both"], default="ndjson",
                    help="ndjson (ambos motores via loader), csv o both (default: ndjson)")
    ap.add_argument("--customers", type=int, default=100_000, help="tamaño del pool de clientes")
    ap.add_argument("--start", default="2025-01-01T00:00:00Z", help="inicio del rango de timestamps (UTC)")
    ap.add_argument("--end", default="2026-10-30T23:59:59Z", help="fin del rango de timestamps (UTC)")
    args = ap.parse_args()

    if args.n <= 0 or args.customers <= 0:
        ap.error("--n y --customers deben ser > 0")
    ts_lo, ts_hi = parse_ts(args.start), parse_ts(args.end)
    if ts_lo >= ts_hi:
        ap.error("--start debe ser anterior a --end")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    path_nd = args.out_dir / "orders.ndjson"
    path_csv = args.out_dir / "orders_pg.csv"
    path_manifest = args.out_dir / "dataset_manifest.json"

    rng = random.Random(args.seed)
    customers = build_customer_pool(rng, args.customers)
    cat_names = list(CATEGORIES)
    cat_weights = [CATEGORIES[c][0] for c in cat_names]

    want_nd = args.format in ("ndjson", "both")
    want_csv = args.format in ("csv", "both")
    f_nd = open(path_nd, "w", encoding="utf-8", newline="\n") if want_nd else None
    f_csv = open(path_csv, "w", encoding="utf-8", newline="") if want_csv else None
    writer = csv.writer(f_csv, lineterminator="\n") if f_csv else None

    sha = hashlib.sha256()
    st = {"items": 0, "completed": 0, "ge_cutoff": 0, "q2_match": 0,
          "items_gt50": 0, "bytes_json": 0}
    t0 = time.perf_counter()

    try:
        for i in range(1, args.n + 1):
            doc = build_order(rng, customers, ts_lo, ts_hi, cat_names, cat_weights)
            line = json.dumps(doc, separators=(",", ":"))
            raw = line.encode("utf-8")
            sha.update(raw)
            sha.update(b"\n")
            st["bytes_json"] += len(raw) + 1

            if f_nd:
                f_nd.write(line + "\n")
            if writer:
                writer.writerow((doc["order_id"], line))

            done = doc["payment"]["status"] == "completed"
            late = doc["timestamp"] >= Q2_CUTOFF
            st["items"] += len(doc["items"])
            st["items_gt50"] += sum(1 for it in doc["items"] if it["price"] > Q1_PRICE_THRESHOLD)
            st["completed"] += done
            st["ge_cutoff"] += late
            st["q2_match"] += done and late

            if i % 50_000 == 0:
                print(f"  {i:>9,} / {args.n:,} docs  ({time.perf_counter() - t0:6.1f} s)", file=sys.stderr)
    finally:
        for fh in (f_nd, f_csv):
            if fh:
                fh.close()

    elapsed = time.perf_counter() - t0
    n = args.n
    stats = {
        "documentos": n,
        "paises_distintos": len(GEO),
        "categorias_distintas": len(CATEGORIES),
        "grupos_q1_esperados": len(GEO) * len(CATEGORIES),
        "items_totales": st["items"],
        "items_por_orden_promedio": round(st["items"] / n, 3),
        "items_gt50_q1": st["items_gt50"],
        "pct_items_price_gt_50_Q1": round(100 * st["items_gt50"] / max(st["items"], 1), 2),
        "ordenes_completed": st["completed"],
        "pct_status_completed": round(100 * st["completed"] / n, 2),
        "ordenes_timestamp_ge_2026_01_01": st["ge_cutoff"],
        "pct_timestamp_ge_2026_01_01": round(100 * st["ge_cutoff"] / n, 2),
        "ordenes_que_cumplen_filtro_Q2": st["q2_match"],
        "pct_filas_que_cumplen_filtro_Q2": round(100 * st["q2_match"] / n, 2),
        "bytes_json_total": st["bytes_json"],
        "bytes_json_promedio_por_doc": round(st["bytes_json"] / n, 1),
        "segundos_generacion": round(elapsed, 1),
    }
    manifest = {
        "generador": "synthetic_data_generator.py",
        "parametros": {"n": n, "seed": args.seed, "customers": args.customers,
                       "start": args.start, "end": args.end, "format": args.format},
        "python": platform.python_version(),
        "sha256_flujo_json": sha.hexdigest(),
        "archivos": {k: str(p) for k, p in (("ndjson", path_nd if want_nd else None),
                                            ("csv_pg", path_csv if want_csv else None)) if p},
        "estadisticas": stats,
    }
    path_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\nDataset generado:", file=sys.stderr)
    for k, v in stats.items():
        print(f"  {k:<36} {v}", file=sys.stderr)
    print(f"  {'sha256_flujo_json':<36} {manifest['sha256_flujo_json']}", file=sys.stderr)
    print(f"\nManifiesto: {path_manifest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
