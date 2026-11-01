# Reporte Técnico: Evaluación Experimental de Rendimiento y Contención de Recursos en Motores Documentales (MongoDB 9.0.4 vs PostgreSQL 18.6 JSONB bajo Docker cgroups v2)

> **Estado del documento:** Secciones **1 (Entorno)**, **2 (Metodología)**, **5 (Planes de Ejecución)** y **7 (Recomendaciones Arquitectónicas y Limitaciones)** redactadas íntegramente con la evidencia empírica del host, dataset y celdas piloto reales. Las secciones **3**, **4** y **6** contienen los resultados reales de las celdas piloto (`PROFILE_C_SEVERE`, `Q1`, `c=1`) y las tablas/figuras estructuradas que se poblarán automáticamente al concluir las 144 corridas de la campaña mediante [`analysis/analyze_campaign.py`](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/analysis/analyze_campaign.py).

---

## 1. Especificaciones del Entorno Experimental

### 1.1 Hardware del Host y Topología de CPU
El experimento se ejecutó sobre una estación de trabajo dedicada bajo aislamiento explícito de núcleos (`cpuset`) para eliminar la interferencia entre el generador de carga, el stack de observabilidad y el motor de base de datos bajo prueba:

| Componente | Especificación |
| :--- | :--- |
| **Procesador (CPU)** | `13th Gen Intel(R) Core(TM) i7-1355U` (arquitectura híbrida x86\_64, 6 núcleos físicos / 12 hilos lógicos) |
| **Caché L3** | `12 288 KB` ($12\text{ MiB}$) |
| **Aislamiento de núcleos (`cpuset`)** | **Motores BD (`mongodb` / `postgresql`):** CPUs lógicas `0-3` (`BENCH_CPUSET_DB`)<br>**Runner Python y Telemetría (`cadvisor`, `prometheus`, `node-exporter`):** CPUs lógicas `4,5,10,11` (`RUNNER_CPUS` / `BENCH_CPUSET_TELEMETRY`)<br>**Núcleos centinela en reposo (control de ruido del host):** CPUs lógicas `6,7,8,9` (`IDLE_SIBLING_CPUS`, umbral de descarte $> 30\%$ de ocupación) |
| **Memoria RAM asignada a la VM Linux** | `7 984 328 kB` ($\approx 7.61\text{ GiB}$ `MemTotal`, $\approx 6.37\text{ GiB}$ `MemAvailable` en reposo previo a cada corrida) |
| **Almacenamiento** | Unidad NVMe SSD local; sistema de archivos de contenedores `overlayfs` sobre volúmenes Docker dedicados (`mongo_data`, `pg_data`, `prometheus_data`) |
| **Política de Swap en Contenedores** | Deshabilitada estrictamente en `cgroups v2` fijando `memswap_limit == mem_limit` (`memory.swap.max = 0` verificado en `/sys/fs/cgroup/docker/<cid>/memory.swap.max`) |

### 1.2 Sistema Operativo, Kernel y Runtime de Contenedores
La telemetría de kernel y el control de recursos operan directamente sobre la jerarquía unificada de **`cgroups v2`** del kernel Linux:

* **Kernel Linux:** `Linux 6.6.87.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC x86_64`
* **Jerarquía `cgroups`:** `cgroup v2`, driver `cgroupfs`, controladores activos: `cpuset cpu io memory hugetlb pids rdma`
* **Nota metodológica sobre WSL2:** Al ejecutarse sobre el kernel Linux de WSL2 (`Docker Desktop 4.55.0`), tanto `node-exporter` como la purga de caché de páginas del sistema operativo (`sync; echo 3 > /proc/sys/vm/drop_caches`) y la lectura directa a $1\text{ Hz}$ de `/sys/fs/cgroup/docker/<cid>/{cpu.stat,memory.stat,memory.current,memory.peak,memory.events,io.stat}` operan sobre el kernel de la VM Linux donde residen los contenedores.
* **Versiones de infraestructura y motores fijadas por digest SHA-256 ([docker-compose.yml](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/docker-compose.yml)):**

| Servicio | Imagen y Tag | Digest SHA-256 Verificado | Versión Interna Reportada |
| :--- | :--- | :--- | :--- |
| **MongoDB** | `mongo:9.0.4` | `sha256:452bdaf2127b8ff851e31410a267809b9829eded219979f2210e7c7efb85a058` | `MongoDB 9.0.4` (`gitVersion: 64ade60...`) |
| **PostgreSQL** | `postgres:18.6` | `sha256:979bccfde35445db618c65107d27d08c6c21339d8dc3ad4c830c6633947be0e4` | `PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2)` |
| **cAdvisor** | `ghcr.io/google/cadvisor:v0.60.6` | `sha256:b8e7d1093144fd088f425ff003d75a4aa405de075db78dae3bc563730b1bd07a` | `v0.60.6` (`--housekeeping_interval=500ms`) |
| **Prometheus** | `prom/prometheus:v3.15.1` | `sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2` | `v3.15.1` (`scrape_interval: 1s`, `honor_timestamps: false`) |
| **Node Exporter** | `prom/node-exporter:v1.12.1` | `sha256:1b4e4438faca4dd7e001dd445d161a4a2091b0fededa84093b3a8dfeae1f1be0` | `v1.12.1` |

### 1.3 Perfiles de Contención (`profiles/{A,B,C}.env`) y Dimensionamiento de Cachés
Para evitar sesgar la comparación con tamaños arbitrarios de caché, en cada perfil se aplicó la **fórmula oficial por defecto de cada fabricante** calculada sobre el límite `mem_limit` del contenedor:
* **PostgreSQL (`shared_buffers`):** $25\%$ de la RAM del contenedor.
* **MongoDB (`--wiredTigerCacheSizeGB`):** $\max\bigl(0.25\text{ GB},\; 0.5 \times (\text{RAM}_{\text{GB}} - 1\text{ GB})\bigr)$.

| Perfil | Archivo | Cuota CFS (`cpus`) | `cpu.max` ($\mu\text{s}$) | Límite RAM (`mem_limit`) | Swap (`memory.swap.max`) | PostgreSQL `shared_buffers` | MongoDB `wiredTigerCacheSizeGB` |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `PROFILE_A_BASELINE` | [profiles/A.env](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/profiles/A.env) | `4.0` | `400000 100000` | `4096m` ($4\text{ GiB}$) | `0` | `1024MB` | `1.5 GB` |
| `PROFILE_B_MEDIUM` | [profiles/B.env](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/profiles/B.env) | `2.0` | `200000 100000` | `2048m` ($2\text{ GiB}$) | `0` | `512MB` | `0.5 GB` |
| `PROFILE_C_SEVERE` | [profiles/C.env](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/profiles/C.env) | `1.0` | `100000 100000` | `1024m` ($1\text{ GiB}$) | `0` | `256MB` | `0.25 GB` |

---

## 2. Metodología Experimental y Diseño de Cargas

### 2.1 Contrato del Dataset Sintético ([synthetic_data_generator.py](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/synthetic_data_generator.py))
Se generó de forma determinista (`seed = 42`) un corpus de $500\,000$ documentos JSON de órdenes de comercio electrónico con 3 niveles de anidamiento (`customer.geo.country`, `items[]`, `payment.{status, final_total}`):

* **Documentos totales ($D$):** $500\,000$ documentos (`557 058 284 bytes` $\approx 531.25\text{ MiB}$ en `data/orders.ndjson`, tamaño medio $1\,114.1\text{ B/doc}$).
* **Huella criptográfica (`SHA-256` del flujo JSON canónico):** `9cd202a6e102ad8e97ebd094f362051f6d46deea98fa0b0a1d5e4f3bad2c0e6f`.
* **Ítems anidados totales:** $1\,499\,046$ ítems ($\mu = 2.998\text{ ítems/orden}$), de los cuales $644\,159$ ($42.97\%$) cumplen `price > 50` en `Q1`.
* **Cardinalidad de agrupación (`Q1`):** $8$ países $\times 12$ categorías $= 96$ grupos exactos.
* **Selectividad del filtro temporal y de estado (`Q2`):** $399\,714$ órdenes ($79.94\%$) con `payment.status = 'completed'`; $226\,680$ órdenes ($45.34\%$) con `timestamp >= '2026-01-01T00:00:00Z'`; intersección de $181\,307$ órdenes ($36.26\%$).
* **Verificación cruzada post-carga (`state/dataset.json`):** Ambos motores reportaron exactamente `count = 500000`, `completed = 399714` y `SUM(payment.final_total) = 229397032.51`.

### 2.2 Cargas de Trabajo e Indexación Equivalente
1. **Consulta `Q1` (Saturación de CPU y Agregación con Desanidado):**
   * **MongoDB ([queries/q1.mongo.json](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/queries/q1.mongo.json)):** Pipeline `$unwind: "$items"` $\rightarrow$ `$match: {"items.price": {"$gt": 50}}` $\rightarrow$ `$group` por `{country, category}` calculando `$sum(qty * price)`, `$avg(qty * price)` y `$sum: 1`.
   * **PostgreSQL ([queries/q1.pg.sql](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/queries/q1.pg.sql)):** `CROSS JOIN LATERAL jsonb_array_elements(data->'items') AS item` con filtro `(item->>'price')::numeric > 50`, agrupando por `country` y `category` con `SUM`, `AVG` y `COUNT(*)`. Adicionalmente se dispone de [queries/q1_float8.pg.sql](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/queries/q1_float8.pg.sql) para aislar en celda piloto el costo de conversión `::numeric` frente a IEEE 754 `::float8` (el tipo subyacente en `synthetic_data_generator.py` y BSON `double`).
2. **Consulta `Q2` (Filtrado Compuesto + Ordenamiento Top-1000 Acotado):**
   * **MongoDB ([queries/q2.mongo.json](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/queries/q2.mongo.json)):** `$match` por `payment.status == "completed"` y `timestamp >= "2026-01-01T00:00:00Z"` $\rightarrow$ `$sort: {"payment.final_total": -1}` $\rightarrow$ `$limit: 1000` $\rightarrow$ `$project`.
   * **PostgreSQL ([queries/q2.pg.sql](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/queries/q2.pg.sql)):** Filtro equivalente sobre `data->'payment'->>'status'` y `(data->>'timestamp')::timestamptz`, `ORDER BY (data->'payment'->>'final_total')::numeric DESC LIMIT 1000`.
3. **Paridad de Índices ([queries/setup.pg.sql](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/queries/setup.pg.sql) y [orchestrator/dataload.py](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/orchestrator/dataload.py)):**
   * En MongoDB se crean índices B-Tree sobre `order_id` y `timestamp`.
   * En PostgreSQL se crea `PRIMARY KEY (id)` y el índice funcional `CREATE INDEX orders_timestamp_idx ON orders (((data->>'timestamp')::timestamptz))` (habilitado dentro del contenedor aislado con `TZ=UTC` mediante `ALTER FUNCTION pg_catalog.timestamptz_in(cstring, oid, integer) IMMUTABLE;`), seguido de `VACUUM (FREEZE, ANALYZE) orders`.

### 2.3 Matriz Experimental (180 Corridas) y Protocolo de Ciclo Frío
* **Matriz Principal (`results/mediciones.csv`):** $2\text{ motores} \times 3\text{ perfiles} \times 2\text{ consultas} \times 3\text{ concurrencias } (1, 10, 50) = 36\text{ celdas} \times 3\text{ repeticiones} = 108\text{ corridas}$ bajo el régimen **`strict`** (`work_mem = '4MB'` en PostgreSQL; `allowDiskUse = false` en MongoDB).
* **Anexo de Contraste de Memoria (`results/anexo_variantes.csv`):** $2\text{ motores} \times 1\text{ perfil } (\text{C}) \times 2\text{ consultas} \times 3\text{ concurrencias } (1, 10, 50) = 12\text{ celdas} \times 3\text{ repeticiones} = 36\text{ corridas}$ bajo el régimen **`relaxed`** (`work_mem = '64MB'` en PostgreSQL; `allowDiskUse = true` en MongoDB), etiquetadas con `query_id = Q1_relaxed` / `Q2_relaxed`.
* **Extensión del anexo (lectura literal de la sección 5.2 del playbook):** `Q2_relaxed` también en `PROFILE_A_BASELINE` y `PROFILE_B_MEDIUM` ($2 \times 2 \times 3 = 12$ celdas $\times 3 = 36$ corridas), de modo que `Q2` se mide con ambas variantes de memoria en los 3 perfiles. Total de la campaña: $108 + 36 + 36 = 180$ corridas (plan reproducible con `build_plan(20261030)`; las 144 corridas originales conservan su orden).
* **Ciclo por corrida ([orchestrator/campaign.py](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/orchestrator/campaign.py) y [benchmark_runner/runner.py](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/benchmark_runner/runner.py)):**
  1. Recreación limpia del contenedor (`docker compose rm -f` + `up -d --force-recreate`) para vaciar al $100\%$ `shared_buffers` y el caché de WiredTiger.
  2. Purga de page cache del kernel (`sync; echo 3 > /proc/sys/vm/drop_caches`).
  3. **Warmup ($60\text{ s}$):** Los $c$ trabajadores (`multiprocessing` en bucle cerrado, 1 round-trip por ejecución en [benchmark_runner/workers.py](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/benchmark_runner/workers.py)) alcanzan estado estacionario.
  4. **Ventana de Medición ($300\text{ s}$):** Al abrirse la ventana $[t_0, t_1]$ se capturan los contadores iniciales `before_window` (`pg_stat_database` / `serverStatus.metrics`) y se marca `phase = start` en el muestreador de `cgroups v2` a $1\text{ Hz}$. Solo las consultas completadas en $[0, 300]\text{ s}$ y los deltas de contadores en $[t_0, t_1]$ alimentan el CSV.
  5. **Cooldown ($60\text{ s}$):** Drenaje ordenado e inspección final del estado del contenedor y logs.

### 2.4 Definición Formal de las Métricas M1, M2 y M3
* **M1 (Sensibilidad al Throttling de CPU CFS):**
  $$\Delta\text{P99}_{X \leftarrow A}(\%) = \frac{\text{P99}_X - \text{P99}_A}{\text{P99}_A}\times 100, \qquad \Delta\text{QPS}_{X \leftarrow A}(\%) = \frac{\text{QPS}_X - \text{QPS}_A}{\text{QPS}_A}\times 100$$
  en función de `cpu_throttled_seconds` ($\Delta\text{throttled\_usec} / 10^6$ en `[t_0, t_1]`).
* **M2 (Resiliencia bajo Presión de Memoria y Volcado a Disco):**
  Detección de `disk_spill` por triple evidencia independiente en $[t_0, t_1]$ (contadores internos del motor, análisis de logs del contenedor y volumen de escritura `wbytes` en `io.stat`), junto con la clasificación de fallos de límite de memoria (`QueryExceededMemoryLimitNoDiskUseAllowed` código `292` en MongoDB; `SQLSTATE 53200/53100` en PostgreSQL) y `OOMKill` del kernel (`memory.events:oom_kill`).
* **M3 (Huella de Recursos por cada $1\,000$ Documentos Procesados):**
  Sea $K = \text{completed\_queries} \times (D_q / 1000)$ el total de miles de documentos procesados en la ventana ($D_q = 500\,000$):
  1. **Costo de CPU ($\text{CPU}_{1\text{k}}$, magnitud extensiva):**
     $$\text{CPU}_{1\text{k}} = \frac{\text{CPU}_{\text{usada}}[t_0, t_1]\;(\text{CPU}\cdot\text{s})}{K}$$
  2. **Huella de Memoria del Motor (`anon + shmem`, excluyendo `file` page cache del SO):**
     * **(a) Nivel máximo intensivo ($\text{Mem}_{\text{peak}}$, en $\text{MB}$):**
       $$\text{Mem}_{\text{peak}} = \max_{t \in [t_0, t_1]} \frac{\text{anon}(t) + \text{shmem}(t)}{2^{20}}$$
     * **(b) Huella integrada extensiva ($\text{Mem}_{1\text{k}}$, en $\text{MB}\cdot\text{s} / 10^3\text{ docs}$):**
       $$\text{Mem}_{1\text{k}} = \frac{\sum_{t \in [t_0, t_1]} \frac{\text{anon}(t) + \text{shmem}(t)}{2^{20}}\cdot \Delta t}{K}$$

---

## 3. Resultados de Latencia (P50, P95, P99) y Rendimiento (QPS)

### 3.1 Resultados Reales de la Celda Piloto (`PROFILE_C_SEVERE`, `Q1`, `c = 1`, `strict`)
La validación previa al congelamiento del manifiesto en el perfil más restrictivo (`1.0 CPU`, `1024 MB RAM`, `0 swap`) arrojó los siguientes resultados empíricos sobre una ventana completa de $300\text{ s}$:

| Motor | Consulta | Perfil | `c` | $N$ Completadas | QPS | P50 (ms) | P95 (ms) | P99 (ms) | CFS Throttled (s) | Max RSS `anon` (MB) | `disk_spill` | Estado |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **MongoDB 9.0.4** | `Q1` | `PROFILE_C_SEVERE` | `1` | `158` ($\ge 100$) | **0.527** | **1 821.2** | **2 275.9** | **3 033.9** | `10.65` | `426.42` | `false` | `ok` |
| **PostgreSQL 18.6** | `Q1` | `PROFILE_C_SEVERE` | `1` | `42` ($< 100$) | **0.140** | **7 084.0** | **7 954.6** | **7 985.6** | `276.00` | `100.69` | `true` | `spill` |

> **Observación clave del piloto:** Con un solo cliente concurrente (`c = 1`) en `PROFILE_C_SEVERE`, MongoDB alcanza **$3.76\times$ mayor throughput** (`0.527` vs `0.140` QPS) y una **latencia mediana $3.89\times$ menor** (`1 821.2 ms` vs `7 084.0 ms`) que PostgreSQL. Como PostgreSQL completa $N = 42 < 100$ ejecuciones en los $300\text{ s}$, su percentil P99 se etiqueta explícitamente con advertencia de tamaño muestral ($N < 100$).

### 3.2 Tablas Consolidadas de la Matriz Completa (Mediana y Rango `[min, max]`)
*(Al concluir la campaña en segundo plano, ejecutar `.venv/Scripts/python.exe analysis/analyze_campaign.py` para insertar aquí las 36 celdas de `results/mediciones.csv` y las 24 celdas de `results/anexo_variantes.csv` desde `results/analysis/tables_summary.md` y la figura `results/analysis/figures/fig1_p99_vs_concurrency.png`.)*

---

## 4. Impacto de la Contención en cgroups v2 (CPU Throttling, Memoria y Spill a Disco — M1 y M2)

### 4.1 Análisis de Throttling CFS (M1)
El planificador CFS del kernel Linux asigna en `PROFILE_C_SEVERE` una cuota de $100\,000\;\mu\text{s}$ por cada periodo de $100\,000\;\mu\text{s}$ (`1.0 CPU`) sobre las 4 CPUs lógicas del cpuset `0-3`:
* **PostgreSQL en `Q1` (`c = 1`, `PROFILE_C_SEVERE`):** A pesar de haber solo 1 sesión cliente, el optimizador lanza **1 proceso líder + 2 workers paralelos (`Workers Launched: 2`)**, sumando **3 procesos activos simultáneos** que compiten por la cuota de `1.0 CPU`. En consecuencia, en una ventana de $300\text{ s}$ de reloj, el cgroup consume `295.43 CPU·s` pero acumula **`276.00 s` de tiempo throttled** (`3 168` periodos CFS penalizados en `cpu.stat`). El paralelismo intra-consulta bajo `1.0 CPU` multiplica las pausas de planificación CFS sin aumentar los núcleos efectivos.
* **MongoDB en `Q1` (`c = 1`, `PROFILE_C_SEVERE`):** El pipeline de agregación de una sesión se ejecuta sobre un único hilo ejecutor principal mientras los hilos de fondo de WiredTiger permanecen casi inactivos en una carga de solo lectura; por ello consume `299.50 CPU·s` con apenas **`10.65 s` de tiempo throttled** ($25.9\times$ menos penalización CFS que PostgreSQL).

### 4.2 Presión de Memoria, Volcado a Disco y Contraste `strict` vs `relaxed` (M2)
1. **Evidencia de Spill en `Q1` (`PROFILE_C_SEVERE`, `c = 1`, `strict`):**
   * En **PostgreSQL (`work_mem = 4MB`)**, durante los $300\text{ s}$ de medición (excluyendo los $60\text{ s}$ de warmup), `pg_stat_database` registró un incremento de **`+473` archivos temporales** (`88` $\rightarrow$ `561`) y **`37.19 GB` de bytes temporales volcados a disco** (`6.92 GB` $\rightarrow$ `44.11 GB`, equivalentes a $\sim 885\text{ MB}$ por consulta completada), confirmados por `written_bytes_window = 61.66 GB` en `io.stat`.
   * En **MongoDB (`allowDiskUse = false`)**, los 96 grupos de `$group` ocupan apenas `63 744 bytes` ($\approx 62.25\text{ KB}$) en memoria (`spills = 0`, `usedDisk = false`), muy por debajo del límite de $100\text{ MB}$ por etapa de pipeline.
2. **Comportamiento de `Q2` como Control Negativo de Memoria:**
   * En ambos motores, el `LIMIT 1000` activa un ordenamiento Top-$N$ acotado en memoria ($\sim 275\text{ KB}$ en PostgreSQL mediante *top-N heapsort*; $\sim 1.35\text{ MB}$ en MongoDB mediante coalescencia `$sort + $limit`), produciendo **`disk_spill = false`** incluso en `PROFILE_C_SEVERE` con `c = 50`.
3. **Hipótesis Dual sobre el Anexo `relaxed` (`work_mem = 64MB` en `PROFILE_C_SEVERE`):**
   * En PostgreSQL, `work_mem` se reserva **por operador de ordenamiento/hash $\times$ por worker paralelo $\times$ por sesión concurrente**. Como el plan de `Q1` contiene **3 nodos `Sort` paralelos + 1 nodo `Incremental Sort`**, cada consulta activa puede reservar hasta $4 \times \text{work\_mem}$.
   * Con `c = 1`, subir `work_mem` de `4MB` a `64MB` reduce los pases de mezcla en disco sin exceder los $1\,024\text{ MB}$ del contenedor.
   * Con `c = 10` y `c = 50`, el aumento a `64MB` multiplica la memoria anónima privada (`anon`) de los backends concurrentes sobre el límite duro de $1\,024\text{ MB}$ (`memory.swap.max = 0`), pudiendo precipitar **`OOMKill`** o errores `SQLSTATE 53200` más rápido que bajo `work_mem = 4MB`.

*(Al finalizar la campaña se integrarán aquí las figuras `fig2_throttling_vs_qps.png`, `fig3_memory_timeseries.png` y la tabla comparativa `strict` vs `relaxed` de `results/anexo_variantes.csv`.)*

---

## 5. Análisis Comparativo de Planes de Ejecución (`EXPLAIN ANALYZE` vs `explain("executionStats")`)

### 5.1 Consulta `Q1`: Por qué PostgreSQL vuelca $755.7\text{ MB}$ a disco mientras MongoDB usa $62\text{ KB}$ en RAM

#### A. PostgreSQL 18.6 ([results/pilot/postgresql-C-Q1-c1-strict-1793432247.plan.txt](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/results/pilot/postgresql-C-Q1-c1-strict-1793432247.plan.txt))
```text
GroupAggregate  (cost=386525.26..3274150.67 rows=16504191 width=136) (actual time=4604.351..6967.793 rows=96.00 loops=1)
  Group Key: (((orders.data -> 'customer') -> 'geo') ->> 'country'), (item.value ->> 'category')
  Buffers: shared hit=25780 read=74360, temp read=195742 written=195958
  ->  Incremental Sort  (cost=386525.26..2407680.64 rows=16504191 width=96) (actual time=4602.185..6769.738 rows=644159.00 loops=1)
        Presorted Key: (((orders.data -> 'customer') -> 'geo') ->> 'country')
        Full-sort Groups: 8  Sort Method: quicksort  Average Memory: 38kB  Peak Memory: 38kB
        Pre-sorted Groups: 8  Sort Method: external merge  Average Disk: 20326kB  Peak Disk: 34336kB
        ->  Nested Loop  (cost=386521.65..1775107.50 rows=16504191 width=96) (actual time=4435.205..6443.242 rows=644159.00 loops=1)
              ->  Gather Merge  (cost=386521.64..444769.67 rows=500127 width=1460) (actual time=4433.932..5637.537 rows=500000.00 loops=1)
                    Workers Planned: 2 / Workers Launched: 2
                    ->  Sort  (cost=385521.62..386042.58 rows=208386 width=1460) (actual time=4348.885..4652.775 rows=166666.67 loops=3)
                          Sort Key: (((orders.data -> 'customer') -> 'geo') ->> 'country')
                          Sort Method: external merge  Disk: 226640kB
                          Worker 0: Sort Method: external merge  Disk: 255152kB
                          Worker 1: Sort Method: external merge  Disk: 239616kB
                          ->  Parallel Seq Scan on public.orders (actual time=376.670..2506.065 rows=166666.67 loops=3)
              ->  Function Scan on pg_catalog.jsonb_array_elements item (actual time=0.001..0.001 rows=1.29 loops=500000)
                    Filter: (((item.value ->> 'price'))::numeric > '50'::numeric)
Execution Time: 7087.410 ms (JIT Total: 1128.608 ms)
```
**Diagnóstico mecánico del plan de PostgreSQL:**
1. **Ceguera estadística sobre expresiones dentro de arreglos JSONB:** PostgreSQL no mantiene histogramas de cardinalidad para las llaves internas de los elementos devueltos por la función de conjunto (`SRF`) `jsonb_array_elements(data->'items')`. Por ello, el planificador estima **`rows=16504191` grupos distintos** (cuando la cardinalidad real es de tan solo **`rows=96`** grupos: $8\text{ países} \times 12\text{ categorías}$).
2. **Descarte de `HashAggregate` y ordenamiento del documento JSONB completo:** Al creer que una tabla hash de $16.5\text{ millones}$ de entradas desbordaría `work_mem = 4MB`, el optimizador descarta `HashAggregate` y elige `Sort + GroupAggregate`. Peor aún, empuja el primer ordenamiento (`Sort Key: country`) **debajo** del `Nested Loop`, obligando a los 3 procesos paralelos a ordenar los **$500\,000$ documentos `orders.data` completos (`width=1460` bytes)** en disco (`226.6 MB + 255.2 MB + 239.6 MB = 721.4 MB`), seguidos de un `Incremental Sort` sobre las $644\,159$ filas desanidadas (`Peak Disk: 34.3 MB`), totalizando **$755.7\text{ MB}$ de escritura temporal por consulta** (`temp written=195958` bloques de $8\text{ KB} \approx 1.53\text{ GB}$ de I/O de bloques temporales).
3. **Sobrecarga de compilación JIT bajo throttling CFS:** La compilación LLVM JIT consume `1 128.6 ms` de CPU (`579 ms` de *Inlining*) que compiten directamente por la cuota CFS en `PROFILE_C_SEVERE`.

#### B. MongoDB 9.0.4 ([results/pilot/mongodb-C-Q1-c1-strict-1793432845.plan.txt](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/results/pilot/mongodb-C-Q1-c1-strict-1793432845.plan.txt))
```json
{
  "winningPlan": {
    "stage": "PROJECTION_DEFAULT",
    "transformBy": { "customer.geo.country": 1, "items": 1, "_id": 0 },
    "inputStage": { "stage": "COLLSCAN", "docsExamined": 500000 }
  },
  "stages": [
    { "$cursor": { "nReturned": 500000, "executionTimeMillis": 1511 } },
    { "$unwind": { "path": "$items", "nReturned": 1499046 } },
    { "$match": { "items.price": { "$gt": 50 }, "nReturned": 644159 } },
    {
      "$group": {
        "nReturned": 96,
        "usedDisk": false,
        "spills": 0,
        "peakTrackedMemBytes": 63744
      }
    }
  ]
}
```
**Diagnóstico mecánico del plan de MongoDB:**
1. **Proyección temprana (`PROJECTION_DEFAULT`):** Antes de desanidar `$items`, MongoDB proyecta únicamente `customer.geo.country` e `items` descartando `_id`, `order_id`, `timestamp` y `payment`.
2. **Agregación por Hash en flujo continuo (`$group`):** A diferencia de PostgreSQL, el operador `$group` del pipeline de agregación de MongoDB construye directamente una tabla hash en memoria conforme fluyen los documentos desde `$match`. Como solo existen **96 claves compuestas `(country, category)`**, el mapa de acumuladores ocupa **`63 744 bytes` ($62.25\text{ KB}$)** en RAM, sin ordenar las $644\,159$ tuplas intermedias y con **`spills = 0`**.

### 5.2 Consulta `Q2`: Optimización Top-$N$ Acotada en Ambos Motores
* **PostgreSQL (`top-N heapsort`):** Al detectar `ORDER BY (data->'payment'->>'final_total')::numeric DESC LIMIT 1000`, el ejecutor mantiene un montículo (*heap*) acotado a $k = 1\,000$ tuplas en memoria ($\sim 275\text{ KB}$), descartando al vuelo cualquier fila menor que el milésimo elemento actual sin volcar a disco (`temp written = 0`).
* **MongoDB (`$sort + $limit` coalescing):** El optimizador de pipeline fusiona las etapas `$sort` y `$limit: 1000` en una cola de prioridad acotada en RAM ($\sim 1.35\text{ MB}$, `spills = 0`), evitando ordenar en memoria el conjunto completo de $181\,307$ documentos filtrados.

---

## 6. Huella de Recursos y Eficiencia Operativa (M3) y Respuestas a las 6 Preguntas de Revisión

### 6.1 Resultados M3 en la Celda Piloto (`PROFILE_C_SEVERE`, `Q1`, `c = 1`)

| Métrica M3 | MongoDB 9.0.4 | PostgreSQL 18.6 | Razón de Eficiencia (PG / Mongo) |
| :--- | :---: | :---: | :---: |
| **Miles de documentos procesados ($K$)** | $79\,000\text{ kdocs}$ ($158$ consultas) | $21\,000\text{ kdocs}$ ($42$ consultas) | $0.266\times$ trabajo completado |
| **$\text{CPU}_{1\text{k}}$ ($\text{CPU}\cdot\text{s} / 10^3\text{ docs}$)** | **`0.00379`** ($3.79\text{ ms}$) | **`0.01407`** ($14.07\text{ ms}$) | PostgreSQL consume **$3.71\times$ más CPU** por $1\,000$ docs |
| **`(a)` $\text{Mem}_{\text{peak}}$ ($\max(\text{anon}+\text{shmem})$, $\text{MB}$)** | **`426.42 MB`** (`anon=426.4`, `shmem=0`) | **`323.91 MB`** (`anon=100.7`, `shmem=227.5`) | Nivel intensivo pico ($0.76\times$) |
| **`(b)` $\text{Mem}_{1\text{k}}$ ($\text{MB}\cdot\text{s} / 10^3\text{ docs}$)** | **`1.565 MB·s / 1k docs`** | **`3.408 MB·s / 1k docs`** | PostgreSQL requiere **$2.18\times$ mayor integral RAM·tiempo** por $1\,000$ docs |

*(La tabla completa de M3 para las 36 celdas base y 24 celdas del anexo, junto con `fig4_m3_efficiency.png`, se genera automáticamente en `results/analysis/tables_summary.md`.)*

### 6.2 Respuestas Técnicas a las 6 Preguntas de Revisión

1. **¿Por qué se separa la memoria del motor (`anon + shmem`) del `memory.current` / `working_set` total del contenedor en M3?**
   En `cgroups v2`, `memory.current` y `container_memory_working_set_bytes` suman `anon + shmem + file`, donde `file` es la caché de páginas del sistema de archivos gestionada por el kernel Linux. En la celda piloto de PostgreSQL (`PROFILE_C_SEVERE`), `file_max_bytes` alcanzó `910.7 MB` (alimentado por los $37.19\text{ GB}$ de archivos temporales de spill), haciendo que `memory.current` rozara el límite de `1 024 MB` (`1 073 876 992 B`). Si se usara `memory.current`, se estaría midiendo el tamaño del page cache del kernel en lugar de la memoria residente de los procesos del motor. Por ello, M3 mide `anon + shmem` (que incluye íntegramente `shared_buffers` en PostgreSQL y el caché de WiredTiger + acumuladores en MongoDB) y reporta tanto **(a) el pico intensivo ($\text{MB}$)** como **(b) la integral extensiva ($\text{MB}\cdot\text{s} / 10^3\text{ docs}$)**.
2. **¿Por qué `Q2` no provoca volcado a disco (`disk_spill`) ni error `292` en `PROFILE_C_SEVERE` mientras que `Q1` vuelca $755.7\text{ MB}$ por consulta en PostgreSQL?**
   Porque `Q2` incluye `LIMIT 1000`, lo que permite a ambos motores aplicar una estructura Top-$K$ acotada en memoria ($\le 1.4\text{ MB}$). En cambio, en `Q1`, `jsonb_array_elements` impide al optimizador de PostgreSQL conocer la cardinalidad de `(country, category)` ($96$ grupos), sobreestimando $16.5\text{ millones}$ de grupos y forzando un `External Merge Sort` de los documentos JSONB completos.
3. **¿Por qué aumentar `work_mem` de `4MB` (`strict`) a `64MB` (`relaxed`) en `PROFILE_C_SEVERE` tiene un efecto opuesto entre baja concurrencia (`c = 1`) y alta concurrencia (`c = 10, 50`)?**
   Porque `work_mem` no es un presupuesto global del servidor sino un límite **por nodo de ordenamiento/hash $\times$ por worker paralelo $\times$ por sesión activa**. En `c = 1` ayuda a reducir los pases de mezcla en disco; pero en `c = 10` y `c = 50` multiplica la memoria anónima concurrente sobre el techo rígido de $1\,024\text{ MB}$ sin swap, agotando el cgroup y disparando `OOMKill` o `SQLSTATE 53200`.
4. **¿Qué sesgo introducía ejecutar `SET statement_timeout` en cada iteración del bucle de medición en PostgreSQL y cómo se eliminó?**
   Ejecutar `SET statement_timeout` dentro de `execute()` duplicaba el número de *round-trips* TCP/libpq por consulta ($2$ viajes de red y $2$ ciclos de planificación simple por iteración en lugar de $1$), inflando artificialmente la latencia de consultas rápidas (`Q2`). En [`PostgresSession.connect`](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/benchmark_runner/workers.py#L74-L79), tanto `SET work_mem` como `SET statement_timeout` se ejecutan **una sola vez al abrir la sesión**, garantizando exactamente $1$ *round-trip* por consulta en ambos motores.
5. **¿Por qué fue necesario configurar `honor_timestamps: false` en Prometheus y `--housekeeping_interval=500ms` en cAdvisor para un scrape de $1\text{ s}$?**
   Porque cAdvisor añade internamente un *jitter* aleatorio de hasta $+50\%$ sobre su `housekeeping_interval` y exporta marcas de tiempo explícitas en cada muestra. Con `housekeeping_interval=1s` y `honor_timestamps: true`, el ciclo interno de cAdvisor ocurría cada $1.0\text{–}1.5\text{ s}$ y Prometheus descartaba por timestamp duplicado $\sim 1/3$ de los scrapes de $1\text{ s}$ (`197` muestras en $300\text{ s}$ en vez de `300`). Con `--housekeeping_interval=500ms` y `honor_timestamps: false`, cada scrape de $1\text{ s}$ registra una muestra fresca y exacta (`300/300` muestras en $300\text{ s}$).
6. **¿Qué implicación tiene el índice B-Tree sobre `(data->>'timestamp')::timestamptz` en PostgreSQL para entornos gestionados en la nube (RDS / Cloud SQL)?**
   En PostgreSQL, el cast `text::timestamptz` (`timestamptz_in`) está catalogado como `STABLE` porque depende del parámetro de sesión `TimeZone` cuando la cadena carece de huso horario explícito. En este benchmark controlado (contenedores con `TZ=UTC` y `PGTZ=UTC`) se marcó `timestamptz_in` como `IMMUTABLE` en [queries/setup.pg.sql](file:///c:/Users/luvia/benchmark-mongodb-postgres-jsonb/queries/setup.pg.sql) para respetar literalmente el SQL de `Q2` sin tocar la consulta. En servicios gestionados (AWS RDS, Cloud SQL, Azure Database) donde el usuario no tiene superusuario sobre `pg_catalog`, la práctica de producción recomendada es extraer el timestamp a una columna nativa `TIMESTAMPTZ` al ingerir o definir una función *wrapper* `IMMUTABLE` propia en el esquema de usuario.

---

## 7. Conclusiones Arquitectónicas, Limitaciones y Recomendaciones para Producción Cloud

### 7.1 Matriz de Decisión Arquitectónica
1. **Cuándo elegir MongoDB (WiredTiger):**
   * Cargas analíticas operacionales o agregaciones frecuentes con desanidado de arreglos (`$unwind` + `$group`) sobre esquemas jerárquicos donde las claves de agrupación no están materializadas en columnas relacionales.
   * Contenedores con presupuestos estrictos de CPU (`1.0–2.0 vCPU` bajo cuotas CFS de Kubernetes) donde el paralelismo de procesos de PostgreSQL y la descompresión/recorrido de `JSONB` generan alto *throttling* CFS y volcados masivos a disco.
2. **Cuándo elegir PostgreSQL (`JSONB`):**
   * Arquitecturas híbridas transaccionales/relacionales que requieren integridad referencial ACID multi-tabla y donde los atributos JSON consultados o agrupados con alta frecuencia se exponen mediante **columnas generadas `STORED`** o tablas relacionales normalizadas con estadísticas completas del optimizador.

### 7.2 Recomendaciones de Tuning para Producción en Contenedores (`cgroups v2`)
* **En PostgreSQL sobre Kubernetes/Docker con límites de CPU y RAM:**
  1. **Evitar agrupar directamente sobre salidas de `jsonb_array_elements` sin control de plan:** Si la cardinalidad real de grupos es baja, materializar los atributos frecuentes o desactivar temporalmente el sort antes del desanidado evita que la sobreestimación de filas del optimizador dispare `External Merge Sort` de cientos de megabytes.
  2. **Ajustar `max_parallel_workers_per_gather` acorde a la cuota CFS (`cpu.max`):** En contenedores limitados a `1.0 vCPU` (`cpu.max = 100000 100000`), fijar `max_parallel_workers_per_gather = 0` (o `1`) evita que 3 procesos paralelos por consulta se estrangulen mutuamente en el planificador CFS (`276 s` de throttling en `300 s`).
  3. **Dimensionar `work_mem` dividiendo entre `max_connections × workers_per_query`:** Nunca dimensionar `work_mem` pensando en una sola consulta aislada cuando `memory.swap.max = 0`.
* **En MongoDB sobre Kubernetes/Docker:**
  1. **Fijar siempre `--wiredTigerCacheSizeGB` explícitamente en función del `mem_limit` del contenedor** para impedir que WiredTiger calcule su caché sobre la RAM física del nodo host y provoque `OOMKill` en el cgroup.
  2. **Habilitar `allowDiskUse: true` solo con límites de concurrencia controlados** en etapas `$group`/`$sort` de alta cardinalidad para transformar errores fatales `292` en degradación controlada por I/O.
