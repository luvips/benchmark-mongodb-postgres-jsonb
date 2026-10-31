---
title: "Análisis de Arquitectura Previa y Hallazgos Técnicos de Ejecución"
type: "architectural-review"
related_documents:
  - "[[playbook_especificacion_tecnica]]"
  - "[[presentacion_contrato_entrega]]"
tags:
  - benchmark
  - mongodb
  - postgresql
  - jsonb
  - execution-plans
  - cgroups-v2
  - telemetry
  - lessons-learned
---

# Análisis de Arquitectura Previa y Hallazgos Técnicos de Ejecución

Este documento consolida las mejores prácticas de ingeniería, correcciones de instrumentación y hallazgos de planes de ejecución (`EXPLAIN`) identificados a partir de pruebas piloto previas sobre el benchmark **MongoDB vs PostgreSQL JSONB**.

---

## 1. Patrones de Ingeniería a Conservar

1. **Aislamiento de CPU (`cpuset`) consciente de topología SMT:**
   - Fijar los contenedores de base de datos a un subconjunto exclusivo de núcleos físicos (`cpuset`) y separar el *runner* y el stack de telemetría (`cAdvisor`, `Prometheus`, `Node Exporter`) en núcleos distintos evita interferencia de planificación en el host.
   - Monitorear la ocupación de los hilos hermanos SMT libres permite detectar ruido externo durante la ventana de medición.
2. **Ausencia de `HEALTHCHECK` interno en los contenedores de los motores:**
   - Ejecutar `mongosh` o `pg_isready` mediante el `HEALTHCHECK` de Docker lanza procesos dentro del mismo `cgroup` medido, consumiendo cuota CFS y páginas de memoria durante `PROFILE_C_SEVERE`. La verificación de disponibilidad debe hacerse desde el host antes de iniciar el *warmup*.
3. **Filtrado en Prometheus por ID de `scope` de contenedor:**
   - Al recrear contenedores con el mismo `container_name`, Prometheus conserva la serie previa durante su ventana de *staleness* (5 minutos). Consultar por `id="/system.slice/docker-<cid>.scope"` garantiza aislamiento total entre corridas.
4. **Doble fuente de telemetría (Prometheus + lectura directa de `/sys/fs/cgroup`):**
   - Si ocurre un `OOMKill`, el contenedor puede desaparecer antes del siguiente *scrape* de Prometheus. Un hilo muestreador sobre `/sys/fs/cgroup/system.slice/docker-<cid>.scope` a `1s` preserva el último estado de `memory.events` (`oom_kill`), `memory.peak`, `memory.stat` y `cpu.stat`.
5. **Generación determinista con manifiesto SHA-256 y `VACUUM (FREEZE, ANALYZE)`:**
   - Congelar tuplas y actualizar estadísticas en PostgreSQL inmediatamente después del `COPY` evita que *autovacuum* o la escritura de *hint bits* contaminen las lecturas durante las corridas.

---

## 2. Fallas Técnicas de Instrumentación a Evitar

### 2.1 Overhead de doble Round-Trip en PostgreSQL (`SET statement_timeout` por consulta)
- **Problema:** Ejecutar `SET statement_timeout = ...` antes de cada `SELECT` dentro del bucle medido duplica los *round-trips* TCP y suma latencia de parseo/planificación del comando `SET` a cada muestra de PostgreSQL, mientras que en MongoDB `maxTimeMS` viaja embebido en el comando `aggregate`.
- **Solución:** Configurar `statement_timeout` una sola vez al abrir la conexión de cada *worker* (p. ej. `SET statement_timeout = '300s'`), midiendo exclusivamente `conn.execute(sql).fetchall()`.

### 2.2 Clasificación rígida de `conn_lost` como fallo de infraestructura
- **Problema:** Clasificar cualquier `conn_lost > 0` (incluso cuando `oom_kills == 0`) como fallo de infraestructura descarta corridas legítimas en `PROFILE_C_SEVERE` (`c=50`), donde la saturación extrema de CPU/memoria puede rechazar o expirar conexiones sin que el kernel dispare `oom_kill`.
- **Solución:** Distinguir entre caída total del servicio antes de medir y errores de conexión bajo saturación severa durante la ventana de medición.

### 2.3 Ventana temporal de `written_bytes` (`io.stat`)
- **Problema:** Calcular `written_bytes` como `last["wbytes"] - first["wbytes"]` incluye los 60 s de *warmup* y los 60 s de *cooldown*, contaminando la detección de *disk spill* por escrituras de bloque.
- **Solución:** Calcular el delta estrictamente entre los *snapshots* del inicio y fin de la ventana de medición (`window_end["wbytes"] - window_start["wbytes"]`).

### 2.4 Subestimación de memoria en PostgreSQL al leer únicamente `anon` al final de la corrida
- **Problema:**
  1. Tomar el valor final (`last.get("anon")`) en lugar del máximo durante la ventana reporta valores cercanos a cero si el contenedor se reinició por OOM.
  2. En cgroups v2, `shared_buffers` de PostgreSQL se contabiliza en `shmem` dentro de `memory.stat`, no en `anon`, mientras que el caché de WiredTiger en MongoDB reside en memoria anónima (`anon`).
- **Solución:** Registrar `max_over_time(container_memory_rss[300s])` desde Prometheus para el esquema CSV estándar, y guardar en metadatos tanto el pico de `anon`, `shmem` como `memory.peak` (`working_set`) para explicar la diferencia arquitectónica en el reporte.

### 2.5 Reseteo de `pg_stat_database` tras un `OOMKill` (`spill: false` falso negativo)
- **Problema:** Cuando el kernel mata un backend de PostgreSQL con `SIGKILL` (señal 9), el `postmaster` aborta todos los procesos hijos, entra en modo recuperación y **reinicia las estadísticas de `pg_stat_database` a cero** (`temp_files = 0`, `temp_bytes = 0`). Si solo se compara `after["temp_files"] > before["temp_files"]`, una corrida que hizo *spill* masivo y terminó en OOM reporta `disk_spill = false`.
- **Solución:** Combinar tres señales independientes dentro de la ventana de medición:
  1. Incremento en `pg_stat_database` (`temp_files` / `temp_bytes`).
  2. Presencia de `temporary file:` en los logs del contenedor (`log_temp_files=0`).
  3. Delta de `wbytes` en `io.stat` del cgroup durante la ventana de 300 s (dado que la carga es 100 % de solo lectura).

---

## 3. Hallazgos Críticos de Motor (`EXPLAIN`) para el Reporte Técnico

### 3.1 Inversión empírica del comportamiento de `Spill` entre `Q1` y `Q2`

1. **`Q2` (`ORDER BY payment.final_total DESC LIMIT 1000`) activa *Top-N Bounded Sort* en ambos motores:**
   - **PostgreSQL:** Detecta `LIMIT 1000` junto al `ORDER BY` y utiliza `Sort Method: top-N heapsort`, manteniendo un *min-heap* en memoria de apenas **$\sim 275\text{ KB}$** por worker. Por ello, cabe holgadamente en `work_mem = 4MB` y no vuelca a disco.
   - **MongoDB:** Aplica optimización de coalescencia `$sort` + `$limit` (`limitAmount: 1000`), consumiendo solo **$\sim 1.35\text{ MB}$** (`peakTrackedMemBytes: 1,347,009`), muy por debajo del límite de 100 MB de `allowDiskUse: false` (`usedDisk: false`, `spills: 0`).

2. **`Q1` (`$unwind`/`jsonb_array_elements` + `GROUP BY country, category`) provoca *Spill* masivo y OOM en PostgreSQL, pero no en MongoDB:**
   - **PostgreSQL:** Al desanidar `jsonb_array_elements(data->'items')` sin estadísticas de cardinalidad interna sobre las llaves JSONB, el optimizador sobreestima **$\sim 16.5\text{ millones}$ de grupos** (frente a los $8 \times 12 = 96$ grupos reales). En consecuencia, descarta `HashAggregate` (estimando que desbordaría `work_mem = 4MB`) y elige un plan de **`Parallel Sort` + `Incremental Sort` + `GroupAggregate`** que ordena las $500\,000$ filas completas (`width=1460` bytes) en disco temporal (**$\sim 755\text{ MB}$ de *temp files* por consulta**). Con `c=50`, los 50 clientes concurrentes agotan los `1024 MB` de `PROFILE_C_SEVERE` y disparan `OOMKill`.
   - **MongoDB:** Ejecuta un `COLLSCAN` con proyección temprana (`customer.geo.country`, `items`) y acumula directamente en una tabla hash en memoria para `$group` sobre los 96 grupos reales, utilizando únicamente **$\sim 64\text{ KB}$ de RAM** (`peakTrackedMemBytes: 64,256`) y `0` spills a disco.

### 3.2 Impacto de selectividad en `Q2` (`IXSCAN` vs `Parallel Seq Scan`)
- Cuando el filtro `timestamp >= '2026-01-01T00:00:00Z'` selecciona $\sim 45\%$ del dataset ($226\,680$ documentos):
  - **PostgreSQL** estima correctamente el costo de páginas y prefiere un `Parallel Seq Scan` sobre la tabla, evitando saltos aleatorios de índice.
  - **MongoDB** elige `IXSCAN` sobre `timestamp_1` seguido de $226\,680$ operaciones `FETCH` para evaluar `payment.status == "completed"`, lo que introduce mayor latencia por indirección documento a documento.

### 3.3 Asimetría de paralelismo intra-consulta (`c=1`)
- Con la configuración por defecto, PostgreSQL lanza hasta 2 *parallel workers* por consulta (`max_parallel_workers_per_gather = 2`), utilizando hasta 3 vCPUs para una sola consulta en `c=1`, mientras que el pipeline de agregación de MongoDB se ejecuta en un único hilo por cursor.
- En `PROFILE_C_SEVERE` (`1.0 CPU`), el paralelismo intra-consulta de PostgreSQL en `c=50` multiplica la contención de procesos sobre una única cuota CFS, incrementando los cambios de contexto y el tiempo *throttled*.
