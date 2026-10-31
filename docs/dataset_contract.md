# Contrato del Dataset (`synthetic_data_generator.py`)

`synthetic_data_generator.py` produce un dataset determinista que cumple estrictamente la Sección 4 y la Enmienda 9.3 de [[playbook_especificacion_tecnica]]. `run_benchmarks.sh validate-data` verifica este contrato antes de cargar en ambos motores.

## 1. Interfaz y Reproducibilidad

```bash
python synthetic_data_generator.py --n 500000 --seed 42 --format ndjson --out-dir data
```

- La misma semilla (`--seed 42`) produce exactamente el mismo flujo JSON byte a byte (`sha256_flujo_json` registrado en `data/dataset_manifest.json`).
- Orden de inserción aleatorio respecto a `timestamp` (sin correlación física entre el orden del heap/colección y el índice temporal).

## 2. Esquema y Niveles de Anidamiento (3 niveles)

| Campo | Nivel | Tipo JSON | Regla y Propósito en el Benchmark |
| :--- | :--- | :--- | :--- |
| `order_id` | 1 | `string` (UUID v4) | Único en minúsculas. Es `PRIMARY KEY (id)` en PostgreSQL e índice simple en MongoDB. |
| `timestamp` | 1 | `string` (ISO-8601 UTC) | Formato fijo `YYYY-MM-DDTHH:MM:SSZ` en `[2025-01-01T00:00:00Z, 2026-10-30T23:59:59Z]`. |
| `customer` | 1 $\rightarrow$ 3 | `object` | Pool de $100\,000$ clientes (`id`, `tier`, `segment`, `geo.{country, state, city, zip, coordinates}`). |
| `items` | 1 $\rightarrow$ 3 | `array<object>` | Longitud $1..5$ con media $= 3.0$ ítems/orden (`sku`, `category`, `brand`, `qty`, `price`, `tax_pct`, `discounts`). |
| `payment` | 1 $\rightarrow$ 3 | `object` | Incluye `method`, `status`, `transaction_id`, `gateway_latency_ms`, subobjeto `totals` (nivel 2/3) y `final_total` numérico (usado en `Q2`). |
| `metadata` | 1 $\rightarrow$ 2 | `object` | `channel`, `user_agent`, `session_id`, `flags.{is_gift, first_purchase, express_shipping, fraud_review}`. |

## 3. Parámetros Estadísticos y Selectividad (`seed = 42`, $N = 500\,000$)

- **Cardinalidades de `Q1`:**
  - `customer.geo.country`: $8$ países (`MX`, `US`, `CO`, `AR`, `ES`, `BR`, `CL`, `PE`).
  - `items[].category`: $12$ categorías (`electronics`, `home`, `fashion`, `sports`, `beauty`, `toys`, `books`, `grocery`, `automotive`, `garden`, `office`, `pet_supplies`).
  - Grupos resultantes en `Q1`: $8 \times 12 = 96$ grupos.
  - Ítems totales desanidados: $\sim 1\,499\,046$ ($\bar{x} = 2.998$ ítems/orden).
  - Selectividad `items.price > 50`: $\sim 42.97\%$ ($644\,159$ ítems).
- **Selectividad de `Q2`:**
  - `payment.status == "completed"`: $\sim 79.94\%$.
  - `timestamp >= "2026-01-01T00:00:00Z"`: $\sim 45.34\%$ ($226\,680$ órdenes).
  - Filtro conjunto (`completed` $\land$ `timestamp >= 2026-01-01`): $\sim 36.26\%$ ($181\,307$ órdenes a ordenar para extraer el `Top-1000`).
- **Tamaño físico:**
  - Promedio $\sim 1\,114\text{ bytes/doc}$ ($\sim 557\text{ MB}$ en NDJSON crudo), permitiendo contrastar holgura de caché en `PROFILE_A_BASELINE` (`4096 MB`) frente a presión combinada de caché + buffers en `PROFILE_C_SEVERE` (`1024 MB`).
