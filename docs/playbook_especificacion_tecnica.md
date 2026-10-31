---
title: "Playbook Técnico: Medición de métricas en bases documentales"
subtitle: "Contención de CPU y memoria en agregaciones JSON: MongoDB frente a PostgreSQL JSONB"
institution: "Universidad Politécnica de Chiapas"
program: "Ingeniería en Tecnologías de la Información e Innovación Digital (IT2iD)"
course: "Bases de Datos en la Nube"
year: 2026
type: "technical-specification"
related_documents:
  - "[[presentacion_contrato_entrega]]"
tags:
  - benchmark
  - mongodb
  - postgresql
  - jsonb
  - wiredtiger
  - cgroups-v2
  - cadvisor
  - prometheus
  - node-exporter
---

# Medición de métricas en bases documentales: Contención de CPU y memoria en agregaciones JSON (MongoDB frente a PostgreSQL JSONB)

## Objetivo

Medir latencia, rendimiento y huella de recursos al ejecutar agregaciones JSON anidadas bajo límites estrictos de CPU (CFS throttling) y memoria con Docker cgroups v2, comparando un motor documental con un motor relacional que almacena documentos en JSONB.

---

## 1. Preguntas sobre las métricas

- **M1:** ¿Cómo cambian la latencia P95 y P99 de las agregaciones sobre documentos anidados cuando se limita de forma estricta la CPU y la memoria?
- **M2:** ¿Qué motor usa de forma más eficiente los buffers de ordenamiento y agrupamiento antes de volcar a disco temporal o fallar por falta de memoria (OOM)?
- **M3:** ¿Cuál es la huella de CPU y memoria por cada $1\,000$ documentos procesados en el motor documental frente al motor con JSONB?

---

## 2. Motores objetivo

| Motor | Modelo de datos / versión | Indexación |
| :--- | :--- | :--- |
| **MongoDB** | Colección `orders` sin sharding; WiredTiger; `latest-stable` | Índice simple en `timestamp` y `order_id` |
| **PostgreSQL** | Tabla `orders (id UUID PRIMARY KEY, data JSONB)`; `latest-stable` | PK en `id`; B-Tree sobre `timestamp` extraído del JSONB |

---

## 3. Perfiles de recursos (Docker / cgroups v2)

| ID | Nombre | CPUs | Memoria | `mem_swappiness` |
| :--- | :--- | :--- | :--- | :--- |
| `PROFILE_A_BASELINE` | Sin contención (Baseline) | `4.0` | `4096m` | `0` |
| `PROFILE_B_MEDIUM` | Contención media | `2.0` | `2048m` | `0` |
| `PROFILE_C_SEVERE` | Contención severa (throttling activo) | `1.0` | `1024m` | `0` |

---

## 4. Especificación del dataset

- **Registros:** $500\,000$ $\cdot$ formato JSON $\cdot$ **3 niveles de anidamiento** $\cdot$ promedio **3 ítems** por registro.
- **Atributos del documento:**
  - `order_id` (`UUID`), `timestamp` (`ISO-8601`)
  - `customer` (objeto: `id`, `tier`, `segment`, `geo[country, state, city, zip, coordinates]`)
  - `items` (arreglo de objetos: `sku`, `category`, `brand`, `qty`, `price`, `tax_pct`, `discounts`)
  - `payment` (objeto: `method`, `status`, `transaction_id`, `gateway_latency_ms`, `totals` / `final_total`)
  - `metadata` (objeto: `channel`, `user_agent`, `session_id`, `flags`)

> [!NOTE]
> En la definición de atributos de la sección 4 se lista `totals` dentro de `payment`, mientras que en las consultas de la sección 5.2 se accede a `payment.final_total` (`data->'payment'->>'final_total'`). El generador de datos sintéticos debe incluir `payment.final_total` (y opcionalmente el subobjeto `payment.totals`) para garantizar compatibilidad exacta con `Q2`.

---

## 5. Cargas de trabajo (consultas)

### 5.1 Q1 — Unwind, Filter & Grouping (saturación de CPU)

Desanidar el arreglo de ítems, filtrar `price > 50`, calcular subtotal (`qty * price`) y agrupar sumatoria y promedio por país y categoría.

#### MongoDB (pipeline)
```json
[
  { "$unwind": "$items" },
  { "$match": { "items.price": { "$gt": 50 } } },
  {
    "$group": {
      "_id": {
        "country": "$customer.geo.country",
        "category": "$items.category"
      },
      "total_revenue": { "$sum": { "$multiply": ["$items.qty", "$items.price"] } },
      "avg_revenue": { "$avg": { "$multiply": ["$items.qty", "$items.price"] } },
      "count": { "$sum": 1 }
    }
  }
]
```

#### PostgreSQL
```sql
SELECT data->'customer'->'geo'->>'country' AS country,
       item->>'category' AS category,
       SUM((item->>'qty')::numeric * (item->>'price')::numeric) AS total_revenue,
       AVG((item->>'qty')::numeric * (item->>'price')::numeric) AS avg_revenue,
       COUNT(*) AS count
FROM orders, jsonb_array_elements(data->'items') AS item
WHERE (item->>'price')::numeric > 50
GROUP BY 1, 2;
```

---

### 5.2 Q2 — Massive Sort & Top-K (estrés de buffer)

Filtrar órdenes completadas en un rango de fechas, ordenar descendente por `payment.final_total` y obtener el `Top-1000` sin índices previos en el total.

#### Variantes de estrés de memoria
- **MongoDB:** `allowDiskUse: false` / `allowDiskUse: true`
- **PostgreSQL:** `SET work_mem = '4MB'` / `SET work_mem = '64MB'`

#### MongoDB (pipeline)
```json
[
  {
    "$match": {
      "payment.status": "completed",
      "timestamp": { "$gte": "2026-01-01T00:00:00Z" }
    }
  },
  { "$sort": { "payment.final_total": -1 } },
  { "$limit": 1000 },
  {
    "$project": {
      "order_id": 1,
      "customer.id": 1,
      "payment.final_total": 1
    }
  }
]
```

#### PostgreSQL
```sql
SELECT data->>'order_id' AS order_id,
       data->'customer'->>'id' AS customer_id,
       (data->'payment'->>'final_total')::numeric AS final_total
FROM orders
WHERE data->'payment'->>'status' = 'completed'
  AND (data->>'timestamp')::timestamptz >= '2026-01-01 00:00:00Z'
ORDER BY (data->'payment'->>'final_total')::numeric DESC
LIMIT 1000;
```

---

## 6. Protocolo de ejecución

- **Concurrencia:** `1`, `10`, `50` clientes.
- **Repeticiones por escenario:** `3`.
- **Ciclo de vida:**
  - **Flush de caché:** `sync; echo 3 >/proc/sys/vm/drop_caches`
  - **Warmup:** `60 s` $\cdot$ **medición:** `300 s` $\cdot$ **cooldown:** `60 s`
- **Mediciones totales:** `36 celdas` de la matriz ($2\text{ motores} \times 3\text{ perfiles} \times 2\text{ queries} \times 3\text{ concurrencias}$); cada celda $\times 3\text{ repeticiones}$.

---

## 7. Telemetría y KPIs

### Stack de Telemetría
- **cAdvisor:** métricas de contenedor (cgroups v2).
- **Node Exporter:** métricas de host (I/O, disco, swap).
- **Prometheus:** `scrape_interval: 1s`.

### Indicadores (KPIs)

| KPI | Unidad | Tipo |
| :--- | :--- | :--- |
| `throughput_qps` | `queries/s` | Performance |
| `latency_p50_ms` | `ms` | Performance |
| `latency_p95_ms` | `ms` | Performance |
| `latency_p99_ms` | `ms` | Performance |
| `container_cpu_cfs_throttled_seconds_total` | `s` | OS / Contention |
| `container_memory_rss_bytes` | `bytes` | OS / Memory |
| `disk_spill_detected` | `boolean` | Engine / Internal |
| `oom_killed` | `boolean` | Stability |

---

## 8. Entregables

### 8.1 Repositorio de código
- `docker-compose.yml` con perfiles de recursos parametrizados
- `synthetic_data_generator.py` (dataset $500\text{k}$)
- `benchmark_runner` (scripts `k6` o `Python` multiproceso)
- `queries/` (agregaciones MongoDB y SQL PostgreSQL)
- `run_benchmarks.sh` (automatización de la matriz de mediciones)

### 8.2 Esquema CSV de mediciones
```csv
timestamp_epoch,engine,profile,query_id,concurrency,run_number,throughput_qps,latency_p50_ms,latency_p95_ms,latency_p99_ms,cpu_throttled_seconds,max_rss_mb,disk_spill,status
```

### 8.3 Secciones del reporte de métricas
1. Especificaciones del host y del entorno Docker
2. Método de medición y verificación del baseline
3. Resultados consolidados (throughput, P95/P99, tiempo de throttling)
4. Análisis gráfico (P99 vs concurrencia, throttling vs QPS, curva RSS)
5. Planes de ejecución (`explain executionStats` frente a `EXPLAIN ANALYZE BUFFERS`)
6. Interpretación: límites de memoria, volcado a disco y throttling de cgroups
7. Recomendaciones técnicas para despliegues documentales en la nube

---

## 9. Enmienda Técnica y Protocolo Experimental Controlado (Reconciliación de Especificación)

Esta sección resuelve las ambigüedades e inconsistencias del `playbook.pdf` original para blindar la reproducibilidad y la defensa ante revisores:

### 9.1 Reconciliación de `Q1`, `Q2` y la Pregunta `M2` (Spill / OOM)
1. **Fenómeno de *Top-N Bounded Sort* en `Q2`:**
   - Al mantener `LIMIT 1000` junto a `ORDER BY payment.final_total DESC`, tanto PostgreSQL (`top-N heapsort`, $\sim 275\text{ KB}$) como MongoDB (`$sort` + `$limit` coalescing, $\sim 1.35\text{ MB}$) acotan la memoria en $O(K)$ en lugar de $O(N)$.
   - Por tanto, en `Q2` con $K=1000$ no se supera el umbral de `4 MB` (`work_mem`) ni el límite de `100 MB` de etapa de bloqueo en MongoDB (`allowDiskUse: false` además nunca vuelca a disco en MongoDB: lanza error `QueryExceededMemoryLimitNoDiskUseAllowed` código `292` si supera 100 MB).
2. **Fenómeno de *GroupAggregate Spill* en `Q1`:**
   - En `Q1`, la falta de estadísticas de cardinalidad sobre `jsonb_array_elements(data->'items')` lleva al optimizador de PostgreSQL a estimar $\sim 16.5\text{ M}$ de grupos (frente a los $8 \times 12 = 96$ reales). Con `work_mem = '4MB'`, descarta `HashAggregate` y ejecuta `Parallel Sort` + `Incremental Sort` + `GroupAggregate`, volcando $\sim 755\text{ MB}$ a disco temporal por consulta y provocando `OOMKill` en `PROFILE_C_SEVERE` ($c=50$).
3. **Regla de control de memoria para `Q1` y `Q2`:**
   - Se fijan explícitamente `work_mem` (PostgreSQL) y `allowDiskUse` (MongoDB) **tanto en `Q1` como en `Q2`**:
     - **Variante `strict` (36 celdas base en `mediciones.csv`):** `allowDiskUse: false` en MongoDB; `SET work_mem = '4MB'` en PostgreSQL.
     - **Variante `relaxed` (contraste de memoria en `anexo_variantes.csv`):** `allowDiskUse: true` en MongoDB; `SET work_mem = '64MB'` en PostgreSQL, ejecutada en `PROFILE_C_SEVERE` para `Q1` y `Q2` con las tres concurrencias del protocolo (`c = 1, 10, 50`, Sección 6) para responder empíricamente **M2**: $2 \times 1 \times 2 \times 3 = 12$ celdas $\times 3$ repeticiones $= 36$ corridas.
     - **Identificación en el CSV:** las filas `relaxed` usan `query_id = Q1_relaxed` / `Q2_relaxed`. Se cambia el *valor*, no el esquema de 14 columnas, de modo que la concatenación de todos los CSV en el estudio posterior no produce claves duplicadas con las filas `strict` de `PROFILE_C_SEVERE`.
4. **Estatus e hipótesis explícitas sobre `Q1`/`Q2` y `relaxed`:** los hallazgos del piloto (`analisis_arquitectura_previa.md`) se tratan como **hipótesis** que la campaña confirma o refuta con `EXPLAIN` y filas del CSV:
   - En `Q2` (`K = 1000`), el ordenamiento acotado (*top-N heapsort* $\approx 275\text{ KB}$ en Postgres; coalescencia `$sort + $limit` $\approx 1.35\text{ MB}$ en Mongo) actúa como **control negativo** de memoria: se espera `disk_spill = false` tanto en `strict` como en `relaxed`.
   - En `Q1` sobre PostgreSQL, `work_mem` es un tope **por operación de Sort/Hash, por conexión y por *parallel worker*** (no un fondo compartido), y no se reserva de golpe sino a medida que crecen los buffers. Como el planificador sobreestima $\sim 16.5\text{ M}$ de grupos, incluso con `work_mem = 64MB` descarta `HashAggregate` y mantiene `Sort + GroupAggregate`, pero permite que cada operador `Sort` acumule hasta `64 MB` en RAM antes de volcar a disco. Por ello se formula la **hipótesis dual**: en `PROFILE_C_SEVERE` (`1024 MB`), la variante `relaxed` (`64MB`) **beneficia a `c = 1`** (menos pasadas de mezcla en disco y menor latencia), pero en **`c = 10` y `c = 50` puede adelantar o agravar el `OOMKill`** frente a `strict` (`4MB`) debido a la multiplicación de buffers por proceso concurrente.

### 9.2 Fórmulas Operacionales Exactas para `M1`, `M2` y `M3`
- **M1 (Degradación de cola bajo contención):**
  $$\Delta P_{99}(\text{Perfil } X) = \frac{P_{99}(\text{Perfil } X, c) - P_{99}(\text{Perfil A}, c)}{P_{99}(\text{Perfil A}, c)}, \quad \text{correlacionado con } \Delta\text{CPU}_{\text{throttled}} = \text{throttled\_s}(t_1) - \text{throttled\_s}(t_0)$$
- **M2 (Eficiencia de buffers antes de Spill u OOM):**
  - Detección por triple evidencia en ventana $[t_0, t_1]$:
    1. Contadores internos: $\Delta\text{spills} > 0$ (`serverStatus.metrics.query.{sort,group}` en Mongo; `pg_stat_database.{temp_files,temp_bytes}` en Postgres).
    2. Logs del motor: `"usedDisk":true` (Mongo) o `"temporary file:"` con `log_temp_files=0` (Postgres).
    3. Escritura en bloque del cgroup: $\Delta\text{wbytes}(t_0, t_1) \ge 32\text{ MiB}$ en `io.stat` bajo carga 100 % de lectura (captura *spills* aun si un `OOMKill` posterior reinicia `pg_stat_database` a 0).
- **M3 (Huella de CPU y memoria por cada $1\,000$ documentos procesados):**
  - **Definición de documentos procesados por consulta ($D_q$):**
    - En `Q1`: $D_{\text{Q1}} = 500\,000$ documentos escaneados ($1\,499\,046$ ítems desanidados).
    - En `Q2`: $D_{\text{Q2}} = 500\,000$ (tamaño de la colección). Se normaliza por el dataset completo y no por los documentos examinados, porque estos dependen del plan elegido ($500\,000$ en `Seq Scan` frente a $226\,680$ en `IXSCAN`); así $\text{CPU}_{1\text{k}}$ mide el costo de responder la misma pregunta sobre el mismo dataset. Los documentos examinados reales se reportan aparte desde `EXPLAIN`.
  - **Huella de CPU por $1\,000$ documentos ($\text{CPU}_{1\text{k}}$):**
    $$\text{CPU}_{1\text{k}} \left[\frac{\text{CPU}\cdot\text{s}}{10^3\text{ docs}}\right] = \frac{\text{cpu\_usage\_seconds}(t_1) - \text{cpu\_usage\_seconds}(t_0)}{\text{completed\_queries} \times (D_q / 1000)}$$
  - **Huella de Memoria para M3: (a) Nivel máximo $\text{Mem}_{\text{engine}}$ y (b) Integral $\text{Mem}_{1\text{k}}$:**
    - El CSV oficial reporta `max_rss_mb` a partir de `max_over_time(container_memory_rss[300s])` (que en cgroups v2 refleja `anon`).
    - **(a) Nivel máximo del motor en la ventana (`m3_memory_peak_mb`, en MB):** Como PostgreSQL aloja `shared_buffers` en memoria compartida (`shmem`) y WiredTiger vive en memoria anónima (`anon`), el nivel pico del motor es:
      $$\text{Mem}_{\text{engine}} \text{ [MB]} = \max_{t \in [t_0, t_1]} \left(\frac{\text{anon}(t) + \text{shmem}(t)}{2^{20}}\right)$$
      Este nivel es una **magnitud intensiva** (no se acumula con el tiempo ni con el número de consultas); dividirlo entre $500$ solo lo escalaría por una constante idéntica en todas las celdas, por lo que se reporta en **MB absolutos** (sin usar `memory.peak` del cgroup, que incluye el warmup y el *page cache* `file`).
    - **(b) Huella integrada de memoria por cada $1\,000$ documentos (`m3_memory_mb_s_per_1k_docs`, en $\text{MB}\cdot\text{s} / 10^3\text{ docs}$):** Para contar con una **magnitud extensiva** que sí acumula en el tiempo y es dimensionalmente análoga a $\text{CPU}_{1\text{k}}$, se integra $(\text{anon} + \text{shmem})$ sobre la ventana de medición con muestras cada $\sim 1\text{ s}$, usando como $\Delta t$ el tiempo real medido entre muestras consecutivas (no un valor fijo de $1\text{ s}$, para absorber la deriva del muestreador y los segundos sin lectura; la suma de los $\Delta t$ queda en `engine_mem_integral_seconds` y debe aproximarse a $300\text{ s}$) y se divide entre los miles de documentos procesados:
      $$\text{Mem}_{1\text{k}} \left[\frac{\text{MB}\cdot\text{s}}{10^3\text{ docs}}\right] = \frac{\sum_{t \in [t_0, t_1]} \frac{\text{anon}(t) + \text{shmem}(t)}{2^{20}} \cdot \Delta t}{\text{completed\_queries} \times (D_q / 1000)}$$
    - En el reporte, **M3** se presenta con `max_rss_mb` (CSV), `m3_memory_peak_mb` (nivel en MB) y `m3_memory_mb_s_per_1k_docs` ($\text{MB}\cdot\text{s} / 10^3\text{ docs}$).

### 9.3 Indexación de `timestamp` en PostgreSQL y Tipo de Datos en `Q2`
- **En el dataset (`synthetic_data_generator.py`):** `timestamp` se almacena siempre como string ISO-8601 UTC canónico (`YYYY-MM-DDTHH:MM:SSZ`), en el rango `2025-01-01T00:00:00Z` a `2026-10-30T23:59:59Z` (semilla `42`), donde el corte `>= 2026-01-01T00:00:00Z` selecciona el $45.34\%$ de los documentos ($36.26\%$ combinando `payment.status = 'completed'`).
- **En PostgreSQL (`setup.pg.sql`):** La expresión `(data->>'timestamp')::timestamptz` invoca `timestamptz_in`, marcada por defecto como `STABLE` (dependiente del `TimeZone` de la sesión), lo que impide crear un índice B-Tree directamente. En el contenedor aislado (que corre en `TZ=UTC`), se habilita la indexabilidad exacta de la expresión del playbook:
  ```sql
  ALTER FUNCTION pg_catalog.timestamptz_in(cstring, oid, integer) IMMUTABLE;
  CREATE INDEX IF NOT EXISTS orders_timestamp_idx ON orders (((data->>'timestamp')::timestamptz));
  ```
  De este modo, la consulta `Q2` del playbook se ejecuta **sin alterar un solo carácter de su SQL** (Sección 5.2) y el optimizador dispone del índice B-Tree prometido en la Sección 2.
- **Limitación documentada:** modificar `pg_catalog` requiere superusuario y **no es posible en PostgreSQL administrado** (Amazon RDS, Cloud SQL, Azure Database). En un despliegue en la nube la alternativa es una función propia `IMMUTABLE` (p. ej. `iso_timestamptz(text)`) indexada y referenciada desde la consulta, lo que obliga a modificar el SQL de `Q2`. Esta limitación se discute en la Sección 7 del reporte. El piloto indica que, con la selectividad de $\sim 45\%$, PostgreSQL prefiere `Parallel Seq Scan`, por lo que el impacto esperado del índice en los resultados es bajo.
- **Precisión aritmética en `Q1` (`::numeric` vs `double`):** Se mantiene `::numeric` en el SQL oficial de `Q1` por fidelidad al playbook, y se incluye en la Sección 2/5 del reporte una micro-comparativa contra `::float8` (`queries/q1_float8.pg.sql`) para cuantificar el overhead de CPU de la aritmética decimal exacta frente a IEEE 754 de MongoDB. Se ejecuta fuera de la matriz como celda piloto:
  ```bash
  ./run_benchmarks.sh run-one --engine postgresql --profile A --query Q1_FLOAT8 --concurrency 1
  ./run_benchmarks.sh run-one --engine postgresql --profile A --query Q1 --concurrency 1
  ```

### 9.4 Control de Versiones, Cachés, Reinicio en Frío y Estadística
1. **Imágenes fijadas por versión y digest SHA-256 (`docker-compose.yml`):**
   - Prohibido usar tags mutables sin digest. Se registra en `meta.json` la imagen y versión exacta devuelta por el motor.
2. **Desactivación real de swap en cgroups v2:**
   - En cgroups v2 `mem_swappiness` es ignorado por el kernel; la desactivación efectiva se impone con `memswap_limit: ${BENCH_MEM}` idéntico a `mem_limit: ${BENCH_MEM}` (0 bytes de margen para swap), manteniendo `mem_swappiness: 0` por trazabilidad documental.
3. **Dimensionamiento explícito de cachés internas por perfil (regla por defecto de cada fabricante):**
   - **Criterio:** la variable independiente del experimento es el límite de cgroup (Sección 3); dentro de él, cada motor se configura como lo recomienda su fabricante, que es como se desplegaría en la nube. PostgreSQL: `shared_buffers = 25 %` de la RAM (delega el resto en el *page cache* del SO). MongoDB: `wiredTigerCacheSizeGB = max(0.25, 0.5 × (RAM − 1 GB))`. Igualar presupuestos sería artificial porque los dos motores usan el *page cache* de forma distinta, y dar a WiredTiger el 50 % de toda la RAM (en lugar de su regla) provocaría OOM atribuibles a la configuración y no al motor.
   - `PROFILE_A_BASELINE` (`4096m`): `shared_buffers = 1024MB`, `wiredTigerCacheSizeGB = 1.5`.
   - `PROFILE_B_MEDIUM` (`2048m`): `shared_buffers = 512MB`, `wiredTigerCacheSizeGB = 0.5`.
   - `PROFILE_C_SEVERE` (`1024m`): `shared_buffers = 256MB`, `wiredTigerCacheSizeGB = 0.25` (mínimo soportado por WiredTiger).
4. **Limpieza real de cachés entre corridas:**
   - Antes de cada repetición se **detiene y recrea el contenedor del motor** (vaciando al 100 % `shared_buffers` y el caché de WiredTiger) y luego se ejecuta `sync; echo 3 > /proc/sys/vm/drop_caches` para limpiar el page cache del kernel Linux/WSL2.
5. **Telemetría a `1s` sin repetición de muestras:**
   - `cAdvisor` se configura con `--housekeeping_interval=1s` y `--allow_dynamic_housekeeping=false`, alineado con `scrape_interval: 1s` de Prometheus.
6. **Agregación estadística y `meta.json` acompañante:**
   - Las corridas se ejecutan en orden **aleatorizado por bloques de repetición** (semilla fija).
   - Para cada repetición se calculan `P50`, `P95` y `P99` sobre las muestras completadas en la ventana de $300\text{ s}$. Entre las 3 repeticiones de una celda se reporta la **mediana** (y el rango $[\min, \max]$) en las tablas del reporte.
   - **Regla de tamaño de muestra:** un P99 calculado con $n < 100$ consultas completadas se reporta como **no confiable** (`p99_reliable = false` en `./run_benchmarks.sh summarize`), indicando $n$ junto al valor. `summarize` reporta además cuántas repeticiones aportaron latencias (`runs_with_latency`), ya que las corridas `oom` no tienen percentiles.
   - Cada fila de `mediciones.csv` y `anexo_variantes.csv` tiene un archivo `results/raw/<run_id>/meta.json` con: `n_completed` (número de muestras $n$), `n_censored`, `variant` (`strict`/`relaxed`), `session_options` (`work_mem` / `allowDiskUse`), `cpu_usage_seconds`, `cgroup_throttled_seconds`, `m3_cpu_seconds_per_1k_docs`, `m3_memory_peak_mb`, `m3_memory_mb_s_per_1k_docs`, `engine_mem_max_bytes`, `engine_mem_byte_seconds`, `anon_max_bytes`, `shmem_max_bytes`, `file_max_bytes`, `memory_peak_bytes`, `engine_version` y `limits` (salida de `docker inspect`).

### 9.5 Requisitos de Ejecución, Integridad y Duración
1. **Imágenes:** antes de la primera corrida se ejecuta `docker compose pull`; `./run_benchmarks.sh doctor` verifica que todas las imágenes fijadas por digest existan localmente (MongoDB 9.0 Community se publicó de forma escalonada; si el tag no existe, se fija la última versión estable disponible y se registra en el reporte).
2. **Visibilidad del cgroup:** el runner lee `/sys/fs/cgroup` del contenedor (io.stat, memory.stat, memory.events). Si la ruta no es visible (caso típico: Docker Desktop en Windows, donde los contenedores viven en otra distribución WSL), la corrida **se aborta** en lugar de registrar una fila con telemetría degradada. Se requiere Docker Engine instalado dentro de la distribución WSL2/Linux donde corre el runner.
3. **CPUs:** los `cpuset` asumen 12 CPUs lógicas (motor `0-3`, runner/telemetría `4,5,10,11`, hermanos SMT ociosos `6-9`). `doctor` verifica que existan; en otro host se ajustan `BENCH_CPUSET_*` y `config.py`.
4. **Integridad del código de medición:** el manifiesto SHA-256 protege consultas, perfiles, generador y todo el código que calcula throttling, spill, memoria y M3 (`runner.py`, `telemetry.py`, `engine_stats.py`). Cualquier cambio durante la campaña (incluido un arreglo del agente de incidencias) la detiene con `manifest_violation`; las corridas previas a un cambio deben repetirse.
5. **Duración estimada:** $108$ corridas `strict` $+ 36$ `relaxed` $= 144$ corridas de $\sim 8$ min (recreación del contenedor + $60 + 300 + 60$ s) $\approx 19$ h, más la pasada final de `EXPLAIN` (24 planes).

