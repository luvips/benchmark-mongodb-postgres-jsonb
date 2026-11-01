# Bitácora de supervisión de la campaña (144 corridas)

Registro cronológico de lo observado durante la ejecución. Todas las horas son locales (2026-10-31). Fuente: `state/journal.jsonl`, `results/raw/<run>/`, estado de procesos de Windows y de Docker.

## 1. Estado inicial al comenzar la supervisión (02:37)

| Elemento | Observación |
| :--- | :--- |
| Campaña iniciada | 02:02:21 (PID 13100, hijo 27596, `python -m orchestrator.campaign`) |
| Progreso | 4/144 corridas `ok`, estimado restante ≈ 16.6 h |
| Incidencias del orquestador | 0 |
| Watchdog (`watchdog.ps1`) | **No está corriendo** (no existe `state/watchdog/`) |
| `state/campaign.log` | Vacío (0 bytes) |

### Corridas completadas (journal)

| # | Corrida | Inicio | Fin | Duración | Estado |
| :-: | :--- | :-: | :-: | :-: | :---: |
| 1 | mongodb-C-Q1-c1-r1 | 02:02:22 | 02:09:27 | ≈ 7.1 min | ok |
| 2 | postgresql-C-Q2-c50-r1 | 02:09:27 | 02:16:36 | ≈ 7.2 min | ok |
| 3 | mongodb-A-Q1-c10-r1 | 02:16:36 | 02:23:42 | ≈ 7.1 min | ok |
| 4 | postgresql-B-Q2-c50-r1 | 02:23:42 | 02:30:53 | ≈ 7.2 min | ok |
| 5 | postgresql-C-Q1-c1-r1 | 02:30:53 | — | — | **en curso / comprometida** |

El ritmo es de ≈ 7.1 min por corrida, coherente con 60 s warmup + 300 s medición + 60 s cooldown + recreación del contenedor.

## 2. Incidente 1: Docker Desktop caído durante la corrida 5 (detectado 02:37)

**Síntomas**
- `docker ps` falla: `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine ... The system cannot find the file specified`.
- No hay procesos `Docker Desktop` ni `com.docker.backend`; sólo siguen `wslservice`, `vmmemWSL`, `wslrelay` y `wslhost`. WSL reporta `docker-desktop` en `Running`.
- `results/raw/postgresql-C-Q1-c1-r1/series.csv` mide 0 bytes desde las 02:30:59: la telemetría a 1 Hz nunca registró datos.
- `client-00.jsonl` contiene eventos `conn_lost` de ≈ 5 s cada uno hasta 304.98 s: el cliente no logró conectar a PostgreSQL durante toda la ventana.
- El journal no tiene evento posterior a `run_started` de la corrida 5 (último evento 02:30:53), pero el proceso de la campaña sigue vivo.

**Diagnóstico provisional**
Docker Desktop (el motor) terminó entre 02:30 y 02:34 (los `wslhost` actuales se iniciaron 02:34:49, lo que apunta a un reinicio parcial o cierre de Docker Desktop). La corrida 5 no produjo mediciones válidas.

**Impacto sobre la validez**
- Corridas 1–4: no afectadas (terminaron `ok` antes del fallo).
- Corrida 5 (`postgresql-C-Q1-c1-r1`): **debe descartarse y repetirse**. El orquestador la repite si se usa `pause --now` seguido de `resume`.
- No se modificó ningún archivo protegido ni datos. No se ejecutó ningún comando destructivo.

**Acción requerida del usuario**
Reiniciar Docker Desktop (requiere al usuario según `watchdog_prompt.md`) y luego:
1. `./.venv/Scripts/python.exe -m orchestrator.cli pause --now`
2. `./.venv/Scripts/python.exe -m orchestrator.cli doctor`
3. `./.venv/Scripts/python.exe -m orchestrator.cli resume`
4. Arrancar el watchdog en otra terminal: `powershell -ExecutionPolicy Bypass -File .\watchdog.ps1 -NtfyTopic <tema>`

## 3. Notas para el reporte
- Documentar que hubo una interrupción por caída del motor Docker, que la corrida afectada se descartó y se repitió, y que las demás no se vieron afectadas.
- Evidencia citable: `results/raw/postgresql-C-Q1-c1-r1/client-00.jsonl` (`conn_lost`) y `series.csv` vacío.

## 4. Recuperación del Incidente 1 (≈ 02:39)
- Docker Desktop fue reiniciado por el usuario (contenedores de telemetría arriba a ≈ 02:38).
- Cuando el motor Docker cayó, el propio orquestador registró la incidencia `incidents/20261031-023801` (`compose_failed`, canary `telemetry_start_failed`) y se detuvo con `halted: incident_unresolved` (journal, ts 1793435886). El proceso de la campaña (PID 13100) ya había terminado, por lo que **`pause --now` no fue necesario**.
- `orchestrator.cli doctor`: todos los chequeos ✓ (Docker, cgroups v2, dataset, disco, RAM, host en reposo, **manifiesto SHA-256 intacto**).
- `orchestrator.cli resume` ejecutado; journal: `resumed` y `run_started postgresql-C-Q1-c1-r1` (ts 1793435972, ≈ 02:39:32).
- La corrida 5 se repite desde cero; el intento fallido no produjo datos válidos (series vacío, sólo `conn_lost`). Corridas 1–4 intactas.
- Pendiente: el watchdog sigue sin ejecutarse; sin él, otra caída de Docker detendría la campaña hasta intervención manual.

## 5. Revisiones periódicas

### 03:07 — 7/144 corridas
- Corrida 5 repetida: `postgresql-C-Q1-c1-r1` → `spill` (esperado: coincide con el piloto, QPS 0.147, P99 8 160 ms, throttled 275 s, disk_spill=true; no es fallo). Luego `postgresql-C-Q2-c1-r1` ok (QPS 1.130) y `mongodb-C-Q2-c1-r1` ok (QPS 0.217, P99 4 937 ms).
- En curso: `mongodb-B-Q2-c50-r1`. Último evento hace ≈ 7 min (normal, ~7 min/corrida). Restante ≈ 16.2 h.
- Docker y telemetría sanos; todos los `series.csv` tienen datos; watchdog sin alertas; sin incidencias nuevas (solo la 20261031-023801 ya recuperada).
- Observación para el reporte: en Q2/c=1/perfil C, MongoDB (0.217 QPS) es más lento que PostgreSQL (1.130 QPS), lo opuesto a Q1; revisar en el análisis final.

### 03:46 — 8/144 corridas — ⚠ ALERTA: `telemetry_gap` repetido
- Entre las 03:07 y las 03:37 solo avanzó 1 corrida (7→8). `mongodb-B-Q1-c1-r1` lleva **4 intentos** con `run_discarded: telemetry_gap` (journal ts 1793438105, 1793438740, 1793439434; 4.º intento iniciado 03:37).
- Incidencia nueva `incidents/20261031-032540` (`telemetry_gap`): el orquestador la marcó `incident_resolved` (attempt 1, `no_agent_configured`) a los ~2 min, pero la corrida volvió a fallar por lo mismo.
- `docker ps`: **`bench-cadvisor` aparece `unhealthy`** (los otros dos de telemetría "Up"). Probable causa: cAdvisor no entrega muestras a 1 Hz y Prometheus deja huecos en la serie. Sin confirmar (el shell/Docker responden lentos, comandos `docker` tardan > 20 s).
- El watchdog no intervino (siguen llegando eventos, no es "cuelgue" ni "halted"); su log solo tiene la línea de inicio.
- Impacto: las corridas descartadas no se registran en `mediciones.csv`; las corridas 1–8 registradas no se ven afectadas. Se pierde tiempo de campaña (~1 h sin progreso).
- Acción propuesta (pendiente del usuario): `docker compose --env-file profiles/A.env up -d --force-recreate cadvisor node-exporter prometheus` (permitido por `watchdog_prompt.md`) y revisar `docker logs bench-cadvisor`; no ejecutado porque implica descartar la corrida en curso.

### 04:08 — 8/144 corridas — ⚠ ALERTA AGRAVADA (sin progreso desde ≈ 02:50 netos; 8/144 desde las 03:37)
- `mongodb-B-Q1-c1-r1` acumula **7 intentos fallidos** (`telemetry_gap` ×5, `engine_start_failed` ×1 a las 03:51; 8.º intento en curso desde ≈ 04:03, journal ts 1793441010).
- Incidencia nueva `incidents/20261031-035140` (`engine_start_failed`), cerrada por el orquestador como `incident_resolved` (`no_agent_configured`) tras ≈ 2 min; no corrige la causa.
- `orchestrator.cli status` y `docker ps` **no respondieron en 90 s** (antes tardaban < 5 s): Docker Desktop/WSL2 está degradado (probable relación con `bench-cadvisor unhealthy` de la revisión anterior y con el fallo de arranque del motor).
- Último evento hace ≈ 5 min (el orquestador sigue reintentando, por eso el watchdog no interviene; log del watchdog sin cambios).
- Datos: corridas 1–8 intactas. Los intentos descartados no se escriben en `mediciones.csv`.
- Pendiente del usuario: revisar Docker Desktop (estado/recursos, reiniciarlo si es necesario), luego `pause --now` → `doctor` → `resume`, o recrear telemetría (ver entrada de las 03:46).

#### Actualización 04:15 (resultado de los comandos que habían quedado en segundo plano)
- `status` terminó: 8/144, 3 incidencias registradas. Journal: 8.º intento de `mongodb-B-Q1-c1-r1` descartado por `telemetry_gap` (ts 1793441599, ≈ 04:13) y nueva incidencia `20261031-041319` abierta (`no_agent_configured`, el orquestador no tiene agente para repararla).
- `docker ps` **expiró a los 60 s (exit 124)**: el demonio Docker no responde de forma fiable. Confirma el estado degradado de Docker Desktop/WSL2.
- Acumulado de `mongodb-B-Q1-c1-r1`: 8 intentos descartados, 4 incidencias en total (`compose_failed`, `telemetry_gap` ×2, `engine_start_failed`). Sin corridas nuevas desde las 02:50 salvo las tres de las 03:07.

### 04:38 — 8/144 corridas — ⛔ CAMPAÑA DETENIDA: `incident_budget_exhausted`
- Journal (ts 1793442942, ≈ 04:35:42): `halted` con motivo `incident_budget_exhausted`. `mongodb-B-Q1-c1-r1` acumuló ≥ 10 intentos descartados (`telemetry_gap` en su mayoría, 1× `engine_start_failed`) y 4 incidencias (`20261031-023801`, `-032540`, `-035140`, `-041319`), todas con `no_agent_configured`.
- Contenedores (`state/watchdog/20261031-043607/docker_ps.txt`): `bench-cadvisor`, `bench-prometheus` Exited (0); `bench-node-exporter` Exited (143, SIGTERM). El orquestador los detuvo al hacer halt; no hay contenedores en ejecución.
- Watchdog: detectó el halt a las 04:36:06 (`PROBLEMA: campaña detenida`), creó la carpeta de evidencia `state/watchdog/20261031-043607/` y debía invocar a Claude (modo `claude -p`). Al momento de esta revisión no existen `diagnosis.md` ni `claude.json` en esa carpeta: la autocorrección sigue en curso o no arrancó. **No se interviene desde esta sesión para no competir con el watchdog.**
- Datos: corridas 1–8 intactas; ningún archivo protegido modificado por esta supervisión.
- Causa raíz aún sin confirmar: telemetría (cAdvisor `unhealthy`) y demonio Docker lento/colgado. Pendiente que `diagnosis.md` del watchdog la confirme.

### 05:07 — 8/144 corridas — ⛔ Campaña detenida; el watchdog también terminó
- Estado sin cambios: `halted` (`incident_budget_exhausted`) desde las 04:35:42; no hay eventos nuevos en el journal desde entonces (≈ 31 min). Contenedores de telemetría `Exited`, motores apagados. Docker responde ahora con normalidad.
- Watchdog (`watchdog.log`): 04:36:06 detectó el halt → 04:39:31 invocó a Claude Code (`claude -p`) → **04:40:20 terminó sin `diagnosis.md`** (veredicto vacío) → "Claude pide intervención humana" y **el watchdog salió (exit 2)**. En `state/watchdog/20261031-043607/` solo está la evidencia recolectada; no hay `claude.json` ni `claude.err`, lo que sugiere que la invocación falló al arrancar (a revisar: `claude` en el PATH / sesión iniciada en esa terminal).
- Consecuencia: **ya no hay vigilancia automática ni reanudación**. La campaña seguirá parada hasta intervención manual.
- Causa raíz de los `telemetry_gap` sigue sin confirmar; con Docker ya respondiendo, el siguiente paso es `docker logs bench-cadvisor` antes de reanudar.
- Plan de recuperación propuesto (requiere aprobación del usuario): `doctor` → revisar cAdvisor → `resume` (repite `mongodb-B-Q1-c1-r1`) → relanzar `watchdog.ps1`. Corridas 1–8 intactas.

### 05:37 — 8/144 corridas — sin cambios (campaña detenida)
- `halted: incident_budget_exhausted` desde 04:35:42 (≈ 1 h 02 min sin eventos). Sin incidencias nuevas (4 en total). Contenedores de telemetría `Exited`; motores apagados. Watchdog terminado desde 04:40:20 (sin `diagnosis.md`).
- Esperando decisión del usuario para recuperar (ver entrada de las 05:07). Corridas 1–8 intactas.

### 06:07 — 8/144 corridas — sin cambios (campaña detenida)
- `halted: incident_budget_exhausted` desde 04:35:42 (≈ 1 h 32 min). Sin eventos ni incidencias nuevas. Telemetría y motores apagados; watchdog terminado. Corridas 1–8 intactas. Pendiente decisión del usuario para recuperar.

### 06:37 — 8/144 corridas — sin cambios (campaña detenida ≈ 2 h)
- `halted: incident_budget_exhausted` desde 04:35:42. Sin eventos/incidencias nuevas; telemetría y motores apagados; watchdog terminado. Corridas 1–8 intactas. Sigue pendiente la decisión del usuario.

### 07:07 — 8/144 corridas — sin cambios (campaña detenida ≈ 2 h 30 min)
- `halted: incident_budget_exhausted` desde 04:35:42. Sin eventos ni incidencias nuevas; telemetría y motores apagados; watchdog terminado. Corridas 1–8 intactas. Pendiente decisión del usuario.

### 07:37 — 8/144 corridas — sin cambios (campaña detenida ≈ 3 h)
- `halted: incident_budget_exhausted` desde 04:35:42. Sin eventos ni incidencias nuevas; telemetría y motores apagados; watchdog terminado. Corridas 1–8 intactas. Pendiente decisión del usuario.

### 08:07 — 8/144 corridas — sin cambios (campaña detenida ≈ 3 h 30 min)
- `halted: incident_budget_exhausted` desde 04:35:42. Sin eventos ni incidencias nuevas; telemetría y motores apagados; watchdog terminado. Corridas 1–8 intactas. Pendiente decisión del usuario.

### 08:37 — 8/144 corridas — sin cambios (campaña detenida ≈ 4 h)
- `halted: incident_budget_exhausted` desde 04:35:42. Sin eventos ni incidencias nuevas; telemetría y motores apagados; watchdog terminado. Corridas 1–8 intactas. Pendiente decisión del usuario.

### 09:07 — 8/144 corridas — sin cambios (campaña detenida ≈ 4 h 30 min)
- `halted: incident_budget_exhausted` desde 04:35:42. Sin eventos ni incidencias nuevas; telemetría y motores apagados; watchdog terminado. Corridas 1–8 intactas. Pendiente decisión del usuario.

## 6. Por qué se detuvo (análisis) y recuperación — 09:35

### Cadena de eventos
1. `mongodb-B-Q1-c1-r1` fue descartada por **`telemetry_gap`** una y otra vez. El criterio (`benchmark_runner/runner.py`, `MIN_SAMPLE_RATIO = 0.8`) exige ≥ 240 muestras de 1 Hz en la ventana de 300 s, tanto en Prometheus como en el muestreador de cgroup, y que ambas fuentes de throttling coincidan.
2. Evidencia de las incidencias (`incidents/*/incident.json`):

| Incidencia | Causa | `cgroup_window_samples` | `prometheus_samples` | Mínimo |
| :--- | :--- | :-: | :-: | :-: |
| 20261031-032540 | telemetry_gap | **68** | 253 | 240 |
| 20261031-041319 | telemetry_gap | **67** | **236** | 240 |
| 20261031-035140 | engine_start_failed (`docker compose up mongodb` > 180 s) | — | — | — |

   El muestreador de cgroup obtuvo solo ≈ 22 % de las muestras esperadas (67–68 de 300).
3. En paralelo, el CLI de Docker se volvió lentísimo (`docker ps` y `status` > 60–90 s a las 03:46–04:08, antes < 1 s) y cAdvisor reportó `unhealthy` y avisos de `fsHandler` (cálculo de uso de disco tardando hasta ≈ 20 s). Es decir: **el demonio/VM de Docker Desktop estaba degradado, no los motores ni el código de medición**.
4. Cada descarte consume incidencias; tras varias (`compose_failed`, `telemetry_gap` ×2, `engine_start_failed`), el orquestador se detuvo con **`incident_budget_exhausted`** (04:35:42). Las 4 incidencias quedaron con `no_agent_configured` (el orquestador no tiene un agente que repare).
5. El watchdog lo detectó (04:36), pero su llamada a `claude -p` **no arrancó** (no existe `claude.json`/`claude.err`; `claude` no está en el PATH desde este entorno), así que terminó pidiendo intervención humana y salió. Resultado: ≈ 5 h de campaña parada.

### Causa raíz (grado de certeza)
- **Confirmado:** el halt se debe al presupuesto de incidencias agotado por `telemetry_gap` repetidos en una misma corrida; el muestreo de cgroup quedó muy por debajo del 80 % requerido.
- **Probable, no confirmado:** el muestreador lee `\wsl.localhost\docker-desktop\sys\fs\cgroup` (ruta UNC/9P de WSL2, ver `benchmark_runner/telemetry.py`); con Docker Desktop/WSL2 degradado cada lectura tardó segundos, no milisegundos. En reposo ahora cada lectura tarda < 0.02 s y `docker ps` 0.1 s, por lo que el fallo aparece **bajo carga** y no es permanente. Origen del degradado (fuga/saturación de Docker Desktop tras ≈ 1 h de uso continuo, I/O del propio MongoDB en el perfil B) pendiente de verificar.
- Los datos válidos no se vieron afectados: las corridas descartadas no se escriben en `mediciones.csv`.

### Recuperación (autorizada por el usuario)
- `docker logs bench-cadvisor`: sin errores fatales; terminó por SIGTERM (apagado del orquestador).
- `doctor`: todo ✓ (manifiesto SHA-256 intacto, host en reposo, RAM y disco suficientes).
- `resume` a las 09:35: `resumed` + `run_started mongodb-B-Q1-c1-r1`; Docker, cAdvisor (healthy), Prometheus y node-exporter arriba.
- Watchdog relanzado a las 09:36 (ventana minimizada). **Limitación:** si vuelve a detectar un problema no podrá autocorregir porque `claude` no está en su PATH; solo avisará y saldrá. Para habilitar la autocorrección hay que lanzar el watchdog desde una terminal donde `claude --version` funcione.

### Riesgo abierto
Si `mongodb-B-Q1-c1-r1` vuelve a fallar con `telemetry_gap`, la causa es el rendimiento de Docker Desktop/WSL2 bajo carga y conviene reiniciar Docker Desktop y/o `wsl --shutdown`, no seguir reintentando.

### 09:37 — 8/144 corridas — en marcha tras la recuperación
- `running`; `mongodb-B-Q1-c1-r1` reiniciada a las 09:35:25 (≈ 2 min de ~7). Docker, cAdvisor (healthy), Prometheus y node-exporter arriba; watchdog activo desde 09:36:01. Sin incidencias nuevas (4 históricas). Falta verificar que esta corrida supere el chequeo de telemetría (≥ 240 muestras) hacia las 09:43.

### 09:41 — Watchdog reiniciado con `claude` en el PATH
- Causa del fallo anterior del watchdog: `claude.exe` está en `C:\Users\luvia\.local\bin`, que no estaba en el PATH. El usuario añadió esa carpeta al PATH de usuario.
- El watchdog en ejecución (PID 1480) se detuvo y se relanzó (PID 17784) desde un lanzador que recarga el PATH del registro; verificado: `claude` resuelve a `C:\Users\luvia\.local\bin\claude.exe`. La campaña no se tocó. Ahora el watchdog puede autocorregir (máx. 3 veces).

### 09:48 — 9/144 corridas — recuperación confirmada
- `mongodb-B-Q1-c1-r1` terminó **`ok`** (journal ts 1793461349, ≈ 09:42:29) en su 9.º intento; `series.csv` con 409 líneas (≥ 240 requeridas). Ahora corre `postgresql-B-Q1-c1-r1`. Sin incidencias nuevas (4 históricas). Watchdog activo (PID 17784, con `claude` en el PATH).
- Análisis del log de cAdvisor aportado por el usuario (instancia 08:38–10:35 UTC = 02:38–04:35 locales): sin errores fatales. Avisos benignos: `Failed to get system UUID / machine-id` (cada 5 min) y `could not stat .../overlayfs/<id>: no such file` (contenedores efímeros recreados en cada corrida). Evidencia relevante: los avisos `fsHandler: disk usage and inodes count took Ns` aparecen desde las 09:10 UTC (03:10 local) y crecen hasta **15–20 s** (umbral normal < 2 s). Coincide con el inicio de la degradación (03:07–03:25 local) y respalda que el cuello de botella fue la **E/S del sistema de archivos de la VM de Docker/WSL2**, no los motores ni el orquestador.

### 09:51 — Watchdog relanzado con `-MaxAutoFixes 6`
- A petición del usuario se subió el tope de autocorrecciones de 3 a 6 (parámetro de `watchdog.ps1`, sin modificar el archivo). Nuevo PID 17196; `claude` resuelve a `C:\Users\luvia\.local\bin\claude.exe`. El contador se reinicia a 0 al relanzar. La campaña no se tocó (corriendo `mongodb-B-Q1-c10-r1`).

### 10:10 — 12/144 corridas — normal
- Desde la recuperación (09:35): `mongodb-B-Q1-c1-r1` ok, `postgresql-B-Q1-c1-r1` **spill** (esperado: Q1 en PostgreSQL con `work_mem=4MB` vuelca a disco, como en el piloto), `mongodb-B-Q1-c10-r1` ok, `mongodb-C-Q2-c50-r1` ok. En curso: `postgresql-A-Q2-c10-r1` (`series.csv` 401 líneas). Restante ≈ 15.6 h.
- `mediciones.csv`: 12 filas (10 ok, 2 spill). Sin descartes ni incidencias nuevas (4 históricas); último evento hace ≈ 7 min; Docker/cAdvisor (healthy)/Prometheus/node-exporter arriba; watchdog (máx. 6) sin alertas. Ritmo ≈ 7 min/corrida, sin los `telemetry_gap` anteriores.

### 10:30 — 15/144 originales (15/180 con plan ampliado) — ampliación del plan en curso
- Corridas ok desde las 10:10: `postgresql-A-Q2-c10-r1`, `postgresql-B-Q2-c10-r1`, `postgresql-B-Q2-c1-r1`. En curso: `mongodb-A-Q1-c1-r1` (inicio 10:25). Sin descartes ni incidencias nuevas; Docker/telemetría sanos; watchdog sin alertas.
- **Cambio de plan (autorizado por el usuario):** otra sesión amplió `state/plan.json` de 144 a **180 corridas** añadiendo 36 al final (Q2 `relaxed` en perfiles A y B; 18 + 18). Verificado aquí: las 144 originales son idénticas y en el mismo orden (comparación contra `state/plan.json.bak-144`), `doctor` ✓ y `manifest.changed() = []`. El proceso en ejecución conserva el plan de 144 en memoria, por eso se pidió `pause` (10:30, se pausará al terminar la corrida en curso) para luego `resume` y cargar el plan de 180. Estado `status` ya muestra «15/180».
- Para el reporte: el anexo `relaxed` pasa de 12 celdas (solo perfil C) a incluir Q2 en A y B; la decisión se tomó con la campaña ya iniciada y sin invalidar ninguna corrida medida.

### 10:33 — Plan de 180 cargado
- `paused` a las 10:32:06 (journal ts 1793464326) tras `mongodb-A-Q1-c1-r1` ok; `doctor` ✓ (manifiesto intacto); `resume` a las 10:32:51: proceso nuevo de la campaña (carga `plan.json` de 180), `run_started postgresql-C-Q1-c10-r1`. Progreso **16/180**, restante ≈ 19.4 h. Ninguna corrida perdida (la pausa esperó a que terminara la en curso). Watchdog sin cambios (máx. 6).

### 10:50 — 18/180 corridas — normal
- Nuevas desde las 10:33: `postgresql-C-Q1-c10-r1` **spill** (esperado: Q1 en PostgreSQL con `work_mem=4MB`) y `mongodb-A-Q2-c50-r1` ok. En curso: `postgresql-A-Q1-c10-r1` (`series.csv` creciendo). Restante ≈ 19.2 h.
- `mediciones.csv`: 18 filas (15 ok, 3 spill). Sin descartes ni incidencias nuevas (4 históricas); último evento hace ≈ 4 min; Docker/cAdvisor (healthy)/Prometheus/node-exporter arriba; watchdog sin alertas.

### 11:10 — 18/180 corridas — ⛔ CAMPAÑA DETENIDA OTRA VEZ (`incident_budget_exhausted`, 11:04:47)
- `postgresql-A-Q1-c10-r1` descartada **2 veces por `telemetry_gap`** (ts 1793465650 y 1793466137); tras el segundo reintento el orquestador se detuvo (ts 1793466287). No se abrieron incidencias nuevas en `incidents/` (siguen las 4 anteriores): el presupuesto acumulado de incidencias seguía agotado desde la caída anterior y ya no admite ninguna más.
- **Mismo patrón que a las 03:25–04:35:** ≈ 1 h 40 min después de la recuperación (09:35) empieza el `telemetry_gap`. Ahora `docker version` tarda **8.7 s** (en reposo, a las 09:3x tardaba 0.11 s) → el demonio de Docker Desktop/WSL2 vuelve a degradarse con el uso continuo. Refuerza la hipótesis de la sección 6 (cuello de botella de E/S de la VM de Docker, no los motores ni el código de medición).
- Contenedores: `bench-cadvisor` Exited (0), `bench-prometheus` Exited (137, SIGKILL durante el apagado), `bench-node-exporter` Exited (143). Motores apagados.
- **Watchdog:** detectó el halt a las 11:04:55 e invocó a Claude Code a las 11:07:21 (por primera vez con `claude` en el PATH). `claude.json`/`claude.err` existen pero siguen vacíos y aún no hay `diagnosis.md` → autocorrección en curso. No se interviene desde esta sesión para no competir con él. Dado que Docker está lento, es probable que el veredicto sea `NEEDS_HUMAN` (el prompt lo exige cuando Docker no responde).
- Datos: corridas 1–18 intactas (las 2 descartadas no se registran).

### 11:30 — 18/180 corridas — Veredicto del watchdog: `NEEDS_HUMAN` (campaña sigue detenida)
- Watchdog (con `claude` en el PATH por primera vez): 11:07 invocó al agente → 11:18:57 `diagnosis.md` con `NEEDS_HUMAN` → watchdog salió (exit). Recreó los 3 contenedores de telemetría (acción permitida; arriba y sanos). No modificó archivos; manifiesto intacto. **Ya no hay vigilancia automática.** Evidencia: `state/watchdog/20261031-110457/diagnosis.md`.
- **Diagnóstico del agente:** `benchmark_runner/telemetry.py:156` (`head, *fields = line.split()` en `_cpu_times`) lanza `ValueError` si `/proc/stat` devuelve una línea vacía; ocurre dentro del hilo `Sampler.run` (línea 220, al marcar el inicio de ventana para el indicador de ruido del host), lo que mata el muestreador → `telemetry_gap` → descarte → incidencias → `incident_budget_exhausted` (`MAX_INCIDENTS = 4`, `orchestrator/incident.py:11`).
- **Verificación independiente (supervisor):**
  - ✔ El defecto de código existe tal como se describe (líneas 156/220) y el traceback está en `state/campaign.log` (Thread-49).
  - ✔ El presupuesto de incidencias (4/4) hace que el siguiente `telemetry_gap` detenga la campaña de inmediato (`campaign.py:130`).
  - ⚠ **Sobre-atribución:** `campaign.log` solo contiene **un** traceback (el proceso actual, desde 10:32); no demuestra que *todos* los `telemetry_gap` vengan de ahí. Las incidencias de la madrugada mostraron `cgroup_window_samples` = 67–68 (muestreo lento, no hilo muerto). Lectura más coherente: **una única causa de fondo —degradación de E/S de WSL2/Docker tras ≈ 1.5 h de uso (`docker version` 8.7 s)— con dos síntomas**: lecturas lentas del cgroup y, ocasionalmente, lecturas corruptas/vacías de `/proc/stat` por la ruta UNC `\wsl.localhost\...`, que el código no tolera. Sin confirmar.
  - ⚠ La afirmación «las 18 corridas siguen siendo válidas» es razonable (cada una pasó el chequeo de ≥ 240 muestras antes de registrarse), pero lo comprobó el agente, no una auditoría de datos.
- **Decisiones que corresponden al usuario (no ejecutadas):**
  1. *Mitigar la causa de fondo (sin tocar código):* reiniciar Docker Desktop + `wsl --shutdown`, y reanudar. Probablemente vuelva a degradarse en ≈ 1.5 h → considerar pausas/reinicios programados de Docker entre corridas.
  2. *Subir `MAX_INCIDENTS`* (`orchestrator/incident.py`, archivo **no protegido**; no altera la medición, solo la tolerancia operativa). Sin esto, el siguiente `telemetry_gap` vuelve a detener todo.
  3. *Endurecer `_cpu_times`* (ignorar líneas vacías): es archivo **protegido** → cambia el SHA-256 y obliga a regenerar el manifiesto; las corridas 1–18 quedarían medidas con otra versión del archivo. El cambio no afecta a las métricas (solo al indicador de ruido), pero hay que declararlo en el reporte. Recomendación del supervisor: no hacerlo sin necesidad; probar primero 1 y 2.
  4. Descartar y reiniciar las 18 corridas (opción del agente): **no recomendado**, sin evidencia de que estén comprometidas.

### 11:50 — 18/180 corridas — sin cambios (campaña detenida desde 11:04:47, ≈ 46 min)
- `halted: incident_budget_exhausted`; sin eventos ni incidencias nuevas; watchdog terminado (11:18:57); telemetría arriba (recreada por el agente), motores apagados. `MAX_INCIDENTS` sigue en 4. Corridas 1–18 intactas. Pendiente la decisión del usuario (ver entrada de las 11:30).

### 12:10 — 18/180 corridas — sin cambios (campaña detenida ≈ 1 h 06 min)
- `halted: incident_budget_exhausted` desde 11:04:47; sin eventos ni incidencias nuevas; watchdog terminado; motores apagados. Pendiente decisión del usuario (ver 11:30).

### 12:30 — 18/180 corridas — sin cambios (campaña detenida ≈ 1 h 26 min)
- `halted: incident_budget_exhausted` desde 11:04:47; sin eventos ni incidencias nuevas; watchdog terminado; motores apagados. Pendiente decisión del usuario (ver 11:30).

### 12:37 — 18/180 corridas — La laptop se reinició (12:35:53)
- Windows arrancó a las 12:35:53 (apagado accidental). La campaña ya estaba detenida desde 11:04:47 (`halted`), por lo que **no se perdió ninguna corrida en vuelo**.
- Datos en disco intactos: `results/mediciones.csv` 18 filas (+ cabecera), `results/raw/` 18 carpetas, `state/journal.jsonl` y `state/plan.json` (180) sin cambios. Docker Desktop aún no responde tras el arranque (`npipe ... dockerDesktopLinuxEngine` no existe); sin procesos `python` activos.
- El reinicio sirve además como reinicio limpio de WSL2/Docker (mitigación de la degradación observada). Pendiente: iniciar Docker Desktop, verificar volúmenes/dataset con `doctor`, decidir `MAX_INCIDENTS` y reanudar.

### 12:41 — 18/180 — Campaña reanudada tras el reinicio de la laptop
- Docker Desktop iniciado desde esta sesión a las 12:38 (motor listo en ≈ 10 s). `doctor` ✓ completo: dataset cargado (volúmenes persistieron al reinicio), manifiesto SHA-256 intacto, host en reposo.
- `resume` a las 12:39:40 (ts 1793471980): `run_started postgresql-A-Q1-c10-r1` (la corrida que había fallado por `telemetry_gap`; se repite desde cero). Telemetría healthy. Watchdog relanzado 12:40:22 (máx. 6 autocorrecciones, `claude` en el PATH).
- **`MAX_INCIDENTS` NO se modificó** (sigue en 4; el usuario no confirmó el cambio). Consecuencia: si ocurre otro `telemetry_gap` que agote los reintentos, la campaña se detendrá de inmediato otra vez. Se espera que el arranque limpio de WSL2/Docker retrase la degradación (antes aparecía a ≈ 1 h 40 min de uso: ≈ 14:20).

### 12:48 — 19/180 — `MAX_INCIDENTS` 4 → 12 aplicado
- A petición del usuario («a lo que consideres correcto») se subió `MAX_INCIDENTS` a **12** en `orchestrator/incident.py` (archivo no protegido; el manifiesto sigue intacto, 12 pruebas pasan). Justificación: la degradación de Docker/WSL2 reaparece cada ≈ 1.5 h y agotó el presupuesto de 4 dos veces; 12 da margen para ≈ 3–4 episodios más sin eliminar la protección contra fallos persistentes. **Declarar en el reporte** como ajuste operativo que no afecta a las mediciones.
- Para que el proceso cargara el nuevo valor: `pause` (esperó a que terminara `postgresql-A-Q1-c10-r1` → `spill`, esperado; 19.ª corrida registrada), `doctor` ✓ y `resume` a las 12:48.
- Esa corrida, que antes fallaba con `telemetry_gap`, se completó sin problemas tras el reinicio limpio de WSL2/Docker.

### 12:50 — 19/180 corridas — normal
- En curso `mongodb-A-Q2-c10-r1` (inicio 12:47, ≈ 3 min de ~7). `mediciones.csv`: 19 filas (15 ok, 4 spill). Sin descartes ni incidencias nuevas (4 históricas; límite ahora 12). Último evento hace ≈ 3 min. Docker/cAdvisor (healthy)/Prometheus/node-exporter arriba; `docker version` 0.2 s (sano, sin degradación). Watchdog (PID activo desde 12:40, máx. 6) sin alertas.
- Referencia para vigilar la degradación: la anterior apareció ≈ 1 h 40 min tras reanudar → esperar síntomas hacia las 14:20; indicador adelantado = `docker version` > 2 s.

### 12:50 — 19/180 corridas — campaña REANUDADA por el usuario (no por esta supervisión)
- Cronología (journal/archivos): 12:39:40 `resumed` → `postgresql-A-Q1-c10-r1` terminó **`spill`** a las 12:46:51 (esperado en Q1 de PostgreSQL; se completó a la primera, sin `telemetry_gap`) → 12:46:54 `paused` → 12:47:26 `resumed` → `run_started mongodb-A-Q2-c10-r1`. Watchdog relanzado a las 12:40:22 (máx. 6 autocorrecciones). Sin descartes ni incidencias nuevas (4 históricas).
- **Cambio de código registrado:** `orchestrator/incident.py` modificado a las 12:43:28: `MAX_INCIDENTS` **4 → 12** (con comentario: «tope operativo, no afecta a la medición»). Es la opción 2 propuesta a las 11:30; archivo **no protegido**. El `pause`/`resume` de las 12:46–12:47 se hizo, previsiblemente, para que el proceso recargara esa constante. Verificado aquí: `manifest.changed() = []` (ningún archivo protegido modificado; manifiesto de 02:02 sin cambios).
- `git diff --stat`: cambios sin commitear en `incident.py`, `state.py` (plan de 180), `tests/test_suite.py` y dos docs. Declarar en el reporte: (a) ampliación del plan 144→180, (b) `MAX_INCIDENTS` 4→12; ninguno altera consultas, perfiles, telemetría ni generador.
- Riesgo abierto: la causa de fondo (degradación de Docker/WSL2 tras ≈ 1.5 h) **no se ha mitigado**; se espera que reaparezca hacia las **14:10–14:30**. Ahora hay margen de 8 incidencias antes del halt, pero cada `telemetry_gap` sigue costando ≈ 7–15 min de corrida descartada. Si `docker version` vuelve a tardar > 5 s, conviene reiniciar Docker Desktop (+ `wsl --shutdown`) entre corridas con `pause` → `resume`.
- Restante ≈ 19.1 h → fin estimado ≈ 08:00 del domingo.

### 13:10 — 22/180 corridas — normal
- Nuevas desde las 12:50: `mongodb-A-Q2-c10-r1`, `mongodb-C-Q1-c10-r1`, `mongodb-B-Q2-c1-r1`, todas ok. En curso `mongodb-B-Q2-c10-r1`. `mediciones.csv`: 22 filas (18 ok, 4 spill). Restante ≈ 18.7 h.
- Sin descartes ni incidencias nuevas; último evento hace ≈ 2 min; `docker version` 0.1 s; telemetría healthy; watchdog sin alertas.

### 13:10 — 22/180 corridas — normal
- Nuevas desde las 12:50: `mongodb-A-Q2-c10-r1`, `mongodb-C-Q1-c10-r1`, `mongodb-B-Q2-c1-r1`, todas ok. En curso: `mongodb-B-Q2-c10-r1` (`series.csv` creciendo, 111 líneas). Restante ≈ 18.7 h.
- `mediciones.csv`: 22 filas (18 ok, 4 spill). Sin descartes ni incidencias nuevas (4 históricas); último evento hace ≈ 2 min; `docker version` **0.1 s** (sano); cAdvisor healthy; watchdog (máx. 6) sin alertas. Vigilar la ventana 14:10–14:30 (posible degradación de Docker, ver 12:50).

### 13:30 — 24/180 corridas — aviso temprano: 1 `telemetry_gap` (recuperado)
- Nuevas desde las 13:10: `mongodb-B-Q2-c10-r1` ok; `mongodb-A-Q2-c1-r1` **descartada 1 vez por `telemetry_gap` (journal ts 1793474579, ≈ 13:22)** y repetida con éxito (`ok` a las 13:30:04). El reintento automático funcionó y **no se abrió incidencia nueva** (siguen las 4 históricas). En curso: `postgresql-C-Q1-c50-r1`.
- `mediciones.csv`: 24 filas (20 ok, 4 spill). Costo: ≈ 7 min de corrida perdida.
- Interpretación: primer `telemetry_gap` desde la reanudación (12:47), **≈ 35 min después**, antes de lo previsto (14:10–14:30). Puede ser un hecho aislado o el inicio de la degradación de Docker/WSL2. Ahora `docker version` = 0.1 s y cAdvisor healthy, así que no hay síntoma persistente. Vigilar si se repite en la siguiente revisión; si aparece una segunda en la misma hora, proponer al usuario reiniciar Docker Desktop (+ `wsl --shutdown`) con `pause` → `resume` antes de que se acumulen incidencias (hay margen: `MAX_INCIDENTS` = 12).

### 13:30 — 24/180 corridas — ⚠ aviso: 1 `telemetry_gap` aislado (recuperado solo)
- Nuevas desde las 13:10: `mongodb-B-Q2-c10-r1` ok y `mongodb-A-Q2-c1-r1` **ok tras 1 reintento**: el primer intento fue descartado por `telemetry_gap` a las 13:22:59 (journal ts 1793474579) y el reintento inmediato terminó `ok` a las 13:30:04 (411 muestras en `series.csv`, ≥ 240). No se abrió incidencia (el presupuesto sigue en 4/12). En curso: `postgresql-C-Q1-c50-r1`. `mediciones.csv`: 24 filas (20 ok, 4 spill). Restante ≈ 18.5 h.
- Importante: ocurrió **≈ 43 min** después de reanudar (12:39), mucho antes de lo previsto (≈ 1 h 40) y **con `docker version` en 0.1 s y cAdvisor healthy** → no hay degradación visible de Docker en este caso. Compatible con el defecto esporádico de `_cpu_times` (línea vacía en `/proc/stat`, `telemetry.py:156`) descrito por el agente del watchdog, que mataría el muestreador de forma intermitente y no solo con Docker lento. No se pudo confirmar: `state/campaign.log` no se actualiza con el proceso actual (solo conserva el traceback de las 10:32–11:04), así que no hay traceback del 13:22 para contrastar.
- Impacto: el descarte no se registra en `mediciones.csv`; la corrida válida es la del reintento (~7 min perdidos). Si se vuelve frecuente, cada ocurrencia consume 7 min y, si agota los reintentos de una corrida, abre incidencia (límite 12).
- Para el reporte: contar los `run_discarded` por `telemetry_gap` como incidencia operativa, con su fecha; no afectan a los datos registrados.

### 13:50 — 26/180 corridas — normal (primer resultado `oom`, legítimo)
- Nuevas desde las 13:30: `postgresql-C-Q1-c50-r1` → **`oom`**, `postgresql-A-Q1-c50-r1` → `spill` (esperado). En curso `mongodb-B-Q1-c50-r1`. `mediciones.csv`: 26 filas (20 ok, 5 spill, 1 oom). Restante ≈ 18.2 h. Sin descartes nuevos (13 `run_discarded` acumulados en toda la campaña, el último a las 13:22), sin incidencias nuevas (4/12). `docker version` 0.1 s; telemetría healthy; watchdog sin alertas.
- **Resultado experimental, no fallo de infraestructura:** `postgresql-C-Q1-c50-r1` (PROFILE_C_SEVERE, 1 CPU / 1 GiB, 50 clientes, `work_mem=4MB`): 0 consultas completadas, **89 `oom_kill`** en `memory.events`, 4 335 `conn_lost`, pico de memoria 1 074 008 064 B (≈ el límite de 1 GiB), throttling 765.8 s, `telemetry_ok = True` (287 muestras cgroup, 300 de Prometheus), veredicto `fault=false, status=oom`. Confirma la hipótesis M2 del reporte (§4.2: sin swap, 50 sesiones × sort/hash paralelos agotan el cgroup). Datos para §3.2/§4.2 del reporte; QPS = 0 y latencias vacías en el CSV por diseño.

### 13:50 — 26/180 corridas — resultado experimental relevante: primer `oom` (M2)
- Nuevas desde las 13:30: `postgresql-C-Q1-c50-r1` → **`oom`** (verdicto `oom_kill`, `fault=false`, `telemetry_ok=true`: **es un resultado válido, no una falla de infraestructura**); `postgresql-A-Q1-c50-r1` → `spill`. En curso: `mongodb-B-Q1-c50-r1` (362 líneas de `series.csv`). Sin descartes ni incidencias nuevas (4 históricas); `docker version` 0.1 s; cAdvisor healthy; watchdog sin alertas. El `telemetry_gap` de las 13:22 no se ha repetido.
- **`postgresql / PROFILE_C_SEVERE / Q1 / c=50 / strict` (rep 1):** 0 consultas completadas (QPS 0.000, sin latencias), **89 `oom_kills`** del cgroup, 4 335 eventos `conn_lost` (conexiones caídas por los kills), `cgroup_throttled_seconds` 765.8 s (≈ 2.55× la ventana de 300 s: 50 sesiones compitiendo por 1.0 CPU), pico de memoria `anon+shmem` ≈ 955.7 MB de un límite de 1024 MB (`max_rss_mb` en CSV), `cpu_usage_seconds` 274.3, `work_mem=4MB`, sin swap.
- Lectura para el reporte (M2, sección 4.2): confirma la hipótesis de que PostgreSQL con 50 sesiones concurrentes en el perfil más restrictivo agota el techo duro de 1 GiB (cada sesión asigna `work_mem` por nodo de ordenamiento × workers paralelos) y el kernel mata al postmaster/backends repetidamente; contrasta con `postgresql-A-Q1-c50-r1` (perfil A, 4 GiB): completa con `spill`, QPS 0.180, P99 ≈ 194.8 s, `max_rss` ≈ 1 980 MB. Pendiente de contrastar con MongoDB en C/Q1/c50 cuando se ejecute.
- Nota metodológica: una celda con 0 completadas no produce percentiles; el análisis debe tratarla como «fallo por límite de memoria» y no como latencia infinita.

### 14:12 — ≈ 28/180 corridas — ⚠ DEGRADACIÓN REAPARECE (predicha para 14:10–14:30)
- Journal desde las 13:50: `mongodb-B-Q1-c50-r1` ok; `postgresql-A-Q1-c1-r1` spill (esperado); luego `mongodb-C-Q2-c10-r1` **descartada por `telemetry_gap` (ts 1793477152, ≈ 14:05) y por `runner_exception` (ts 1793477359, ≈ 14:09)**. Incidencia nueva **`20261031-140921`** (`runner_exception`, 5.ª en total; presupuesto `MAX_INCIDENTS` = 12 → quedan 7).
- Detalle de la incidencia: `threading.BrokenBarrierError` en `benchmark_runner/runner.py:107` (`barrier.wait(timeout=60)`): los procesos cliente no llegaron a sincronizarse en 60 s al arrancar la corrida → el sistema (host/Docker/WSL2) está tan lento que no puede ni arrancar los workers a tiempo. Mismo cuadro que a las 03:10–04:35 y a las 11:04, ahora **≈ 1 h 25 min después de la reanudación de las 12:47**.
- Síntomas en la propia supervisión: `orchestrator.cli status` y comandos del shell **no respondieron en 120 s** (a las 13:50 tardaban < 5 s); el orquestador se detuvo en `incident_attempt` (`no_agent_configured`) para la incidencia nueva. Contenedores en `docker ps` (según el volcado de la incidencia): mongodb, cAdvisor (healthy), Prometheus, node-exporter arriba.
- Patrón confirmado por 3.ª vez: **la campaña funciona ≈ 1.5 h y luego se degrada hasta quedar inservible**. Con `MAX_INCIDENTS` = 12 el halt tardará más, pero cada reintento fallido quema ≈ 7 min y no hay garantía de recuperación sin reiniciar Docker.
- **Recomendación al usuario (ahora):** `pause` (o esperar al halt) → reiniciar Docker Desktop → `wsl --shutdown` → `doctor` → `resume`. Mientras tanto no se actúa desde esta sesión. Corridas registradas intactas; las descartadas no se escriben en `mediciones.csv`.

#### Actualización 14:15 (salida de los comandos que habían quedado en segundo plano)
- `status` terminó: **`running`, 28/180**, restante ≈ 18.0 h, corriendo `mongodb-C-Q2-c10-r1` (reintento tras la incidencia; aún sin `series.csv` en ese instante). `docker ps` normal (mongodb Up 4 min, cAdvisor healthy). `docker version` **0.4 s** (0.1 s a las 13:50): degradado pero **no colgado**. Es decir, la campaña **no está detenida**; la lentitud de los comandos de la supervisión indica carga alta en el host. `mediciones.csv`: 28 filas (21 ok, 1 oom, 6 spill).
- Ajuste a la entrada de las 14:12: el cuadro grave (halt/colgado) **no se ha producido todavía**; está en fase de degradación temprana. Se vigilará si `mongodb-C-Q2-c10-r1` se completa y si `docker version` supera 5 s.

### 14:16 — 28/180 corridas — ⚠ ALERTA: 3.er tipo de fallo (`runner_exception`), 5.ª incidencia
- Nuevas desde las 13:50: `mongodb-B-Q1-c50-r1` ok, `postgresql-A-Q1-c1-r1` spill (esperado). **`mongodb-C-Q2-c10-r1`: descartada 2 veces** — `telemetry_gap` (14:05:52, ts 1793477152) y luego **`runner_exception`** (14:09:19, ts 1793477359) → incidencia `20261031-140921` (límite ahora 5/12), marcada `incident_resolved` por el orquestador a los ≈ 5 min y la corrida **reiniciada a las 14:14:50** (tercer intento, en curso).
- Detalle de la excepción (`incident.json`): `threading.BrokenBarrierError` en `benchmark_runner/runner.py:107`, `barrier.wait(timeout=60)` → los 10 procesos de cliente (c=10) **no llegaron a la barrera de arranque en 60 s** (arranque/conexión de workers anormalmente lento). Es un tipo de fallo distinto de los `telemetry_gap` anteriores, pero con la misma firma de fondo: máquina/VM lenta bajo carga.
- Contexto de degradación: ocurre ≈ 1 h 32 min después de reanudar (12:40), justo en la ventana prevista (≈ 1 h 40) para la degradación de WSL2/Docker. `docker version` ya no es 0.1 s sino 0.3 s en esta revisión, y mis comandos de supervisión (`status`, `python`) tardan > 60 s en ejecutarse (la propia laptop está lenta). Docker/cAdvisor aún "Up/healthy".
- Estadísticas: `mediciones.csv` 28 filas (21 ok, 6 spill, 1 oom); 15 `run_discarded` acumulados; incidencias 5/12; watchdog sin alertas (siguen llegando eventos).
- Riesgo: si no se actúa, es probable que se repita el patrón de la madrugada (reintentos que consumen incidencias hasta el halt, ahora con margen de 7 más).
- Acción recomendada al usuario (no ejecutada): en un límite de corrida, `pause` → reiniciar Docker Desktop y `wsl --shutdown` → `doctor` → `resume`. Con la laptop en este estado, un reinicio preventivo cada ≈ 1 h 15 min evitaría los descartes.

### 14:28 — 29/180 — Reinicio preventivo de Docker/WSL2 y reanudación
- Pausa pedida a las 14:17; `mongodb-C-Q2-c10-r1` terminó **ok** en su 3.er intento (14:24:09) y la campaña quedó `paused` (ts 1793478250). Ninguna corrida perdida.
- Reinicio: Docker Desktop cerrado, `wsl --shutdown` (3 s), Docker Desktop relanzado; motor listo en ≈ 10 s. `doctor` ✓ (Docker activo, dataset cargado, manifiesto intacto). `resume` a las ≈ 14:27 (sin cambio de código). `docker version` 0.1 s tras el reinicio; telemetría arrancando; watchdog (PID 9140, desde 12:40) activo.
- Racional/seguimiento: la degradación reapareció ≈ 1 h 32 min tras el reinicio anterior (12:40). Repetir el reinicio preventivo cada ≈ 1 h 15 min (próximo ≈ 15:40) evita los descartes (`telemetry_gap`, `runner_exception`). Contador de incidencias 5/12. Cada reinicio cuesta ≈ 3–4 min de campaña frente a ≈ 7–14 min por descarte.

### 14:33 — Autorización permanente de reinicios preventivos
- El usuario autorizó (14:30) que el supervisor ejecute **sin pedir confirmación** el procedimiento `pause` → reinicio de Docker Desktop + `wsl --shutdown` → `doctor` → `resume`, aprox. cada 70 min desde el último `resumed` (próximo ≈ 15:35–15:40), porque no estará disponible para confirmar.
- Límites declarados: no se tocan archivos protegidos, `data/`, `results/`, el journal, el plan ni `MAX_INCIDENTS`; se detiene y se avisa si `doctor` falla, cambia el manifiesto o el halt es `manifest_violation`/`data_mismatch`. También se aplica el mismo procedimiento si la campaña se detiene por `incident_budget_exhausted`/`incident_unresolved` y `doctor` pasa. El watchdog se relanza si termina.
- Implementación: la revisión periódica (cada 20 min) incluye la rutina de reinicio; scripts auxiliares fuera del repo. Para el reporte: cada reinicio programado debe declararse como intervención operativa (no altera las mediciones; la pausa espera a que termine la corrida en curso).

### 14:30 — 29/180 corridas — Docker Desktop reiniciado entre corridas (por el usuario)
- Cronología: `mongodb-C-Q2-c10-r1` terminó **ok** a las 14:24:09 (tras el reintento, incidencia `20261031-140921` cerrada) → **`paused` 14:24:10** → procesos de Docker Desktop reiniciados a las **14:24:45–14:24:52** → **`resumed` 14:25:19** → `run_started postgresql-B-Q1-c10-r1` (58 líneas de `series.csv`, creciendo). Los contenedores llevan 5 min arriba (recreados); `docker version` **0.1 s**; watchdog (máx. 6) sin alertas.
- Se aplicó la mitigación recomendada (pausa limpia entre corridas + reinicio de Docker Desktop): **no se perdió ninguna corrida** por el reinicio. Restante ≈ 18.1 h.
- `mediciones.csv`: 29 filas (22 ok, 1 oom, 6 spill). Incidencias: 5 de 12.
- Procedimiento a repetir (≈ cada 60–75 min, antes de que reaparezca la degradación): `pause` → esperar `paused` → reiniciar Docker Desktop (y `wsl --shutdown` si es necesario) → `doctor` → `resume`. Próximo punto sugerido: ≈ 15:30–15:40. A declarar en el reporte: reinicios de Docker entre corridas (no alteran la medición: el ciclo por corrida ya recrea contenedores y purga caché).

### 14:50 — 31/180 corridas — normal (1 `telemetry_gap` recuperado; sin reinicio)
- Desde el reinicio de las 14:25: `postgresql-B-Q1-c10-r1` → 1.er intento **descartado por `telemetry_gap`** (14:32:25) y reintento `spill` (esperado, 14:39:30); `postgresql-A-Q2-c50-r1` ok. En curso `mongodb-A-Q1-c50-r1`. `mediciones.csv`: 31 filas (23 ok, 7 spill, 1 oom). Incidencias 5/12 (sin nuevas). Último evento hace ≈ 4 min. `docker version` 0.1 s; telemetría healthy; watchdog sin alertas.
- **Hallazgo:** el `telemetry_gap` ocurrió en la primera corrida **inmediatamente después de un reinicio limpio** de Docker/WSL2 (14:25), con Docker rápido. Es el 2.º descarte aislado con Docker sano (el 1.º: 13:22). Debilita la hipótesis de que la degradación de Docker sea la única causa y refuerza la del defecto esporádico de `_cpu_times` (`telemetry.py:156`, línea vacía en `/proc/stat`). Los reinicios preventivos mitigan los fallos graves (arranque de workers, `engine_start_failed`, halts) pero no eliminan los descartes aislados, que el reintento automático resuelve (≈ 7 min c/u).
- Rutina de reinicio preventivo: **no ejecutada** — han pasado 35 min desde el último `resumed` (14:25:19; umbral 70 → ≈ 15:35) y el único síntoma (el descarte de las 14:32) se recuperó solo con Docker sano; reiniciar no lo habría evitado.

### 14:50 — 31/180 corridas — normal (1 descarte tras el reinicio de Docker)
- Nuevas desde las 14:30: `postgresql-B-Q1-c10-r1` **descartada 1 vez por `telemetry_gap` (ts 1793478745, ≈ 14:32)** — fue la **primera corrida tras el reinicio de Docker (14:25)** — y repetida con éxito (`spill`, esperado en Q1/PostgreSQL); `postgresql-A-Q2-c50-r1` ok. En curso: `mongodb-A-Q1-c50-r1` (237 líneas de `series.csv`). Sin incidencia nueva (5 de 12).
- `mediciones.csv`: 31 filas (23 ok, 1 oom, 7 spill). `docker version` 0.1 s; cAdvisor healthy; watchdog sin alertas; restante ≈ 17.8 h.
- Observación: un `telemetry_gap` justo tras reiniciar Docker sugiere que la **primera corrida posterior al reinicio puede arrastrar un calentamiento de cAdvisor/Prometheus/WSL2** (caché fría del disco virtual). Costo: ≈ 7 min por reinicio. Es otro dato a favor de que la causa está en la capa Docker/WSL2 y no en la medición. Próximo reinicio sugerido: ≈ 15:30–15:40.

### 15:10 — 34/180 corridas — normal (2.º resultado `oom`, legítimo)
- Desde las 14:50: `mongodb-A-Q1-c50-r1` ok, **`postgresql-B-Q1-c50-r1` → `oom`** (PROFILE_B_MEDIUM, 2 GiB, 50 clientes, Q1: 2.º OOM tras el de PROFILE_C; resultado M2 legítimo, QPS 0 por diseño), `postgresql-A-Q2-c1-r1` ok. En curso `mongodb-C-Q1-c50-r1`. `mediciones.csv`: 34 filas (25 ok, 7 spill, 2 oom). Restante ≈ 17.5 h.
- Sin descartes ni incidencias nuevas (5/12); último evento hace ≈ 2 min; `docker version` 0.1 s; telemetría healthy; watchdog sin alertas.
- Reinicio preventivo: no toca todavía (45 min desde el último `resumed` 14:25:19; próximo ≈ 15:35).

### 15:10 — 34/180 corridas — normal; segundo `oom` (PostgreSQL, perfil B)
- Nuevas desde las 14:50: `mongodb-A-Q1-c50-r1` ok; **`postgresql-B-Q1-c50-r1` → `oom`** (resultado válido, no falla de infraestructura); `postgresql-A-Q2-c1-r1` ok. En curso: `mongodb-C-Q1-c50-r1` (158 líneas de `series.csv`). Sin descartes ni incidencias nuevas (5 de 12); `docker version` 0.1 s; cAdvisor healthy; watchdog sin alertas; restante ≈ 17.5 h.
- `mediciones.csv`: 34 filas (25 ok, 2 oom, 7 spill).
- **Q1, c=50, `strict` (rep 1) — comparación parcial para el reporte (M1/M2):**

| Perfil | MongoDB (QPS / P99 s / throttled s / pico MB) | PostgreSQL (QPS / P99 s / throttled s / pico MB) |
| :--- | :--- | :--- |
| A (4 CPU / 4 GiB) | 1.507 / 37.0 / 2.8 / 922 · ok | 0.180 / 194.8 / 1.0 / 1 980 · spill |
| B (2 CPU / 2 GiB) | 0.863 / 63.2 / 595.8 / 832 · ok | **0.000 / — / 391.0 / 1 763 · oom** |
| C (1 CPU / 1 GiB) | en curso | **0.000 / — / 765.8 / 956 · oom** |

  Lectura: con 50 sesiones, PostgreSQL ya agota la memoria en el perfil B (2 GiB; pico ≈ 1 763 MB de `max_rss`) y no solo en C; MongoDB sigue completando consultas en B. En A, MongoDB rinde ≈ 8.4× más QPS que PostgreSQL. Estos son datos de una sola repetición (rep 1 de 3); los rangos y medianas llegarán con las repeticiones 2 y 3.
- Próximo reinicio sugerido de Docker entre corridas: ≈ 15:30–15:40.

### 15:32 — ≈ 35/180 corridas — ⚠ DEGRADACIÓN REAPARECE (≈ 1 h tras el reinicio de las 14:24)
- Desde las 15:10: `mongodb-C-Q1-c50-r1` ok (15:15). Luego `postgresql-C-Q2-c10-r1` **descartada por `telemetry_gap` (ts 1793481938, ≈ 15:25) y por `runner_exception` (ts 1793482140, ≈ 15:29)**; incidencia nueva **`20261031-152900`** (`runner_exception`, 6.ª; quedan 6 de 12). Mismo patrón que a las 14:05–14:09 (`BrokenBarrierError` al sincronizar los clientes).
- `status` y los comandos de la supervisión **no respondieron en 120 s** → host/Docker/WSL2 vuelve a estar muy lento. El orquestador sigue vivo, en `incident_attempt` (`no_agent_configured`).
- Intervalo entre degradaciones tras reinicio de Docker: 14:24 → 15:25 = **≈ 61 min** (antes: 12:47 → 14:05 = 78 min; 09:35 → 11:00 = 85 min). **La ventana útil se acorta** con cada ciclo.
- **Recomendación al usuario ahora:** `pause` → esperar `paused` (la corrida en curso termina o se reintenta) → reiniciar Docker Desktop (+ `wsl --shutdown`) → `doctor` → `resume`. Con la ventana de ≈ 1 h, reiniciar **cada 45 min** evitaría los descartes. Corridas registradas intactas.

#### Actualización 15:36 (salida del comando que había quedado en segundo plano)
- `status`: **`running`, 35/180**, restante ≈ 17.4 h, corrida `postgresql-C-Q2-c10-r1` (reintento tras la incidencia; el contenedor del motor aún no aparecía en `docker ps` y no existía `series.csv`: el orquestador estaba entre intentos). `docker version` **0.2 s**: degradado levemente, no colgado. `mediciones.csv`: 35 filas (26 ok, 2 oom, 7 spill). La campaña **no está detenida**; igual que a las 14:12, la lentitud de los comandos refleja carga alta. Pendiente: confirmar en la próxima revisión que esta corrida se complete.

### 15:50 — 35/180 corridas — sin avance desde las 15:15 (3.er descarte de `postgresql-C-Q2-c10-r1`)
- Desde las 15:32: `postgresql-C-Q2-c10-r1` acumula **3 intentos descartados** (`telemetry_gap` 15:25, `runner_exception` 15:29, **`compose_failed` 15:39**, ts 1793482781); el 4.º intento arrancó a las 15:39:41 y **va bien**: contenedor PostgreSQL arriba 5 min, `series.csv` con 341 líneas (≈ 5.7 min de muestreo a 1 Hz, sin huecos aparentes). Incidencias: 6 de 12 (sin nuevas desde `20261031-152900`).
- Costo: ≈ 35 min sin ninguna corrida registrada (35/180 desde 15:15). `docker version` **0.1 s** y cAdvisor healthy en este momento (la lentitud va y viene); watchdog sin alertas.
- `mediciones.csv`: 35 filas (26 ok, 2 oom, 7 spill). Se confirmará en la próxima revisión que esta corrida cierre bien. Sigue recomendado reiniciar Docker Desktop entre corridas (`pause` → reinicio + `wsl --shutdown` → `doctor` → `resume`): las degradaciones acumulan ≈ 7–10 min perdidos por descarte.

### 15:53 — 36/180 — Reinicio preventivo #2 (autorizado) de Docker/WSL2 y reanudación
- **Motivo:** síntomas repetidos ≈ 66 min tras el reinicio anterior: `postgresql-C-Q2-c10-r1` descartada por `telemetry_gap` (15:25:38), luego `runner_exception` (15:29:00 → incidencia `20261031-152900`, 6/12) y `compose_failed` (15:39); `docker version` ya en 0.3 s en la revisión de las 15:31. Esa corrida necesitó **4 intentos** y terminó `ok` (15:51:55).
- **Procedimiento:** `pause` pedida a las 15:33 → `paused` a las 15:51:57 (tras terminar la corrida en curso; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 10 s) → `doctor` ✓ (Docker activo, dataset cargado, manifiesto intacto) → `resume` a las 15:53:05 → `run_started postgresql-C-Q2-c1-r2`. `docker version` 0.1 s; watchdog (PID 9140) activo. Incidencias 6/12.
- **Observación para el reporte:** el patrón se repite con periodo ≈ 65–95 min (14:12 y 15:29 tras reinicios a 12:40 y 14:25). Cada episodio costó ≈ 7–20 min de campaña en reintentos. Cadencia operativa adoptada: reinicio preventivo a ≈ 60 min de cada `resumed` (siguiente ≈ 16:50), en lugar de 70.

### 16:07 — 37/180 corridas — normal
- Desde el reinicio de las 15:53: `postgresql-C-Q2-c1-r2` ok; en curso `mongodb-B-Q2-c10-r2` (inicio 16:00). `mediciones.csv`: 37 filas (28 ok, 7 spill, 2 oom). Restante ≈ 17.4 h. Sin descartes ni incidencias nuevas (6/12); `docker version` 0.1 s; telemetría healthy; watchdog sin alertas. Reinicio preventivo: no toca (14 min desde el último `resumed`; próximo ≈ 16:53).

### 16:10 — 38/180 corridas — recuperado tras 2.º reinicio de Docker (por el usuario)
- `postgresql-C-Q2-c10-r1` cerró **ok** a las 15:51:55 (4.º intento) → **`paused` 15:51:57** → reinicio de Docker (contenedores de telemetría con 17 min de vida a las 16:10, es decir, recreados ≈ 15:53) → **`resumed` 15:53:05**. Sin descartes desde entonces: `postgresql-C-Q2-c1-r2` ok, `mongodb-B-Q2-c10-r2` ok. En curso: `postgresql-C-Q1-c10-r2` (195 líneas de `series.csv`). Incidencias: 6 de 12 (sin nuevas). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 17.3 h.
- **Primeras corridas de repetición 2** (`-r2`): `postgresql-C-Q2-c1-r2`, `mongodb-B-Q2-c10-r2` — el plan aleatorizado ya entró en la 2.ª repetición de algunas celdas.
- `mediciones.csv`: 38 filas (29 ok, 2 oom, 7 spill).
- Tras el reinicio de las 14:24 la ventana sana duró ≈ 61 min y tras el de las 15:53 ≈ ? (en curso; se anota cuando reaparezca). Próximo reinicio preventivo sugerido: ≈ 16:35–16:45 (a los ≈ 45 min), con `pause` → reinicio → `resume`.

### 16:27 — 39/180 corridas — normal (1 `telemetry_gap` aislado recuperado; sin reinicio)
- Desde las 16:07: `mongodb-B-Q2-c10-r2` ok; `postgresql-C-Q1-c10-r2` con 1 intento **descartado por `telemetry_gap`** (16:14:27) y reintento `spill` (esperado, 16:21:35). En curso `postgresql-B-Q2-c1-r2`. `mediciones.csv`: 39 filas (29 ok, 8 spill, 2 oom). Incidencias 6/12 (sin nuevas). `docker version` 0.1 s; telemetría healthy; watchdog sin alertas.
- **Decisión sobre la rutina de reinicio:** la regla literal incluye `telemetry_gap` como síntoma, pero **no se reinició**: es el 4.º descarte aislado con Docker sano (13:22, 14:32, 16:14 y los de la madrugada con Docker lento), se recupera con el reintento automático y no coincide con la firma de degradación (`docker version` > 2 s, `runner_exception`, `compose_failed`, `engine_start_failed`). Reiniciar costaría ≈ 4 min y no lo habría evitado (ya pasó justo tras un reinicio limpio, 14:32). Umbral temporal: 34 min desde el último `resumed` (15:53:05); el siguiente chequeo que supere 60 min es el de las 17:04.
- Para el reporte: ≥ 3 descartes aislados por `telemetry_gap` con Docker sano en 3.5 h de campaña → ≈ 1 cada 70 min; coste ≈ 7 min c/u (≈ 10 % de sobrecosto). Compatible con el defecto esporádico de `_cpu_times` (`benchmark_runner/telemetry.py:156`); los datos registrados no se ven afectados.

### 16:30 — 40/180 corridas — normal (1 descarte recuperado)
- Nuevas desde las 16:10: `postgresql-C-Q1-c10-r2` **descartada 1 vez por `telemetry_gap` (ts 1793484867, ≈ 16:14)**, repetida con éxito (`spill`, esperado); `postgresql-B-Q2-c1-r2` ok. En curso: `postgresql-B-Q1-c10-r2` (118 líneas de `series.csv`). Incidencias: 6 de 12 (sin nuevas; el reintento automático bastó). `docker version` 0.1 s; cAdvisor healthy; watchdog sin alertas; restante ≈ 17.0 h.
- `mediciones.csv`: 40 filas (30 ok, 2 oom, 8 spill).
- **Corrección a la hipótesis del 14:50:** este gap ocurrió ≈ 21 min después del reinicio de Docker (tras dos corridas sin problema), no en la primera corrida posterior; la idea de «calentamiento tras el reinicio» **no se sostiene**. Los descartes aislados de la jornada (13:22 `mongodb-A-Q2-c1`, 14:32 `postgresql-B-Q1-c10`, 16:14 `postgresql-C-Q1-c10`) son esporádicos y los absorbe el reintento (≈ 7 min c/u); los graves (`runner_exception`/`BrokenBarrierError`, `compose_failed`) aparecen solo con la degradación prolongada.
- Próximo reinicio preventivo sugerido: ≈ 16:40–16:50 (≈ 45–55 min tras el de las 15:53).

### 16:47 — 42/180 corridas — normal (3.er `oom`, legítimo)
- Desde las 16:27: `postgresql-B-Q2-c1-r2` ok, `postgresql-B-Q1-c10-r2` spill (esperado), **`postgresql-C-Q1-c50-r2` → `oom`** (PROFILE_C, repetición 2: reproduce el OOM de la repetición 1, buena señal de consistencia para M2). En curso `mongodb-C-Q1-c10-r2`. `mediciones.csv`: 42 filas (30 ok, 9 spill, 3 oom). Restante ≈ 16.8 h.
- Sin descartes ni incidencias nuevas (6/12); `docker version` 0.1 s; telemetría healthy; watchdog sin alertas. Reinicio preventivo: 54 min desde el último `resumed` (15:53:05) → aún bajo el umbral de 60; se ejecutará en la revisión de las 17:04.

### 16:51 — 43/180 corridas — ⚠ Docker vuelve a degradarse (aún sin descartes graves)
- Nuevas desde las 16:30: `postgresql-B-Q1-c10-r2` spill; `postgresql-C-Q1-c50-r2` **oom** (2.ª repetición del mismo resultado: coherente con r1); `mongodb-C-Q1-c10-r2` ok. En curso: `mongodb-C-Q2-c50-r2` (recién iniciada 16:50:17). Incidencias: 6 de 12 (sin nuevas). `mediciones.csv`: 43 filas (31 ok, 3 oom, 9 spill).
- **`docker version` = 10.8 s** (0.1 s a las 16:30) y una consulta `docker ps` tardó ≈ 1 min: degradación otra vez, **≈ 58 min tras el reinicio de las 15:53** (ventanas sanas: 85 → 78 → 61 → ≈ 58 min).
- Falsa alarma aclarada: en un `docker ps` apareció un contenedor `hopeful_poincare`; fue el nombre transitorio del contenedor de MongoDB durante el `--force-recreate` de la corrida (a continuación `docker ps -a` mostró solo `bench-mongodb` y los 3 de telemetría). Ningún contenedor ajeno a la campaña.
- **Recomendación al usuario (ahora, antes de que haya descartes):** `pause` → esperar `paused` → reiniciar Docker Desktop (+ `wsl --shutdown`) → `doctor` → `resume`.

### 17:07 — 44/180 corridas — síntoma + 74 min: se inicia reinicio preventivo #3
- Desde las 16:47: `mongodb-C-Q1-c10-r2` ok; `mongodb-C-Q2-c50-r2` con 1 intento **descartado por `runner_exception`** (16:57:00) y reintento `ok` (17:05:11). En curso `postgresql-B-Q2-c10-r2` (inicio 17:05). `mediciones.csv` ≈ 44 filas. Incidencias 6/12 (sin nuevas). `docker version` 0.1 s; telemetría healthy; watchdog sin alertas.
- 74 min desde el último `resumed` (15:53:05) y un `runner_exception` (firma de degradación, a diferencia del `telemetry_gap` aislado) → pausa pedida a las 17:07; el reinicio de Docker/WSL2, `doctor` y `resume` siguen cuando la pausa llegue (se registrará abajo).

### 17:10 — 44/180 corridas — normal (1 descarte recuperado, sin reinicio de Docker)
- `mongodb-C-Q2-c50-r2` **descartada 1 vez por `runner_exception` (ts 1793487418, ≈ 16:56)**, repetida con éxito (`ok`, 17:05:11). No se abrió incidencia nueva (6 de 12). En curso: `postgresql-B-Q2-c10-r2` (321 líneas de `series.csv`). `docker version` volvió a **0.1 s**; cAdvisor healthy; watchdog sin alertas; restante ≈ 16.6 h.
- `mediciones.csv`: 44 filas (32 ok, 3 oom, 9 spill).
- La degradación de las 16:51 (10.8 s) fue **transitoria** y no se reinició Docker esta vez: costó 1 corrida repetida (≈ 8 min). Confirma que la lentitud es intermitente y que el reintento automático la absorbe mientras no se prolongue. Los reinicios preventivos reducen descartes pero no son estrictamente necesarios si `docker version` vuelve a la normalidad.

### 17:15 — 45/180 — Reinicio preventivo #3 completado
- `paused` a las 17:12:19 (tras `postgresql-B-Q2-c10-r2` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 10 s, 17:13:18) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` ≈ 17:14. `docker version` 0.2 s; watchdog activo; incidencias 6/12. Próximo reinicio preventivo ≈ 18:15 (60 min desde el `resumed`), evaluado en la revisión de las 18:24 o antes si hay síntomas graves (`runner_exception`, `compose_failed`, `engine_start_failed`, `docker version` > 2 s).

### 17:27 — 46/180 corridas — normal
- Desde el reinicio de las 17:14: `postgresql-C-Q2-c50-r2` ok; en curso `postgresql-C-Q1-c1-r2`. `mediciones.csv`: 46 filas (34 ok, 9 spill, 3 oom). Restante ≈ 16.3 h. Sin descartes ni incidencias nuevas (6/12); `docker version` 0.1 s; telemetría healthy; watchdog sin alertas. Reinicio preventivo: 13 min desde el último `resumed` (17:13:35); próximo ≈ 18:15.

### 17:30 — 47/180 corridas — normal (3.er reinicio de Docker por el usuario)
- `postgresql-B-Q2-c10-r2` ok (17:18:58) → **`paused` 17:18:59** → reinicio de Docker (telemetría con 17 min de vida a las 17:30 → recreada ≈ 17:13–17:19) → **`resumed` 17:20:15**. Sin descartes desde entonces: `postgresql-C-Q2-c50-r2` ok, `postgresql-C-Q1-c1-r2` spill (esperado). En curso: `mongodb-B-Q1-c10-r2` (162 líneas de `series.csv`). Incidencias: 6 de 12 (sin nuevas). `docker version` 0.1 s; cAdvisor healthy; watchdog sin alertas; restante ≈ 16.2 h.
- `mediciones.csv`: 47 filas (34 ok, 3 oom, 10 spill). Reinicios de Docker entre corridas hasta ahora: 14:24, 15:53, 17:19 (≈ cada 80–90 min, sin pérdida de corridas). Para el reporte: ver sección 6 de esta bitácora y declarar estos reinicios.

### 17:35 — Autorización ampliada (usuario ausente)
- El usuario salió y autorizó «todo»; si la laptop se bloquea, la supervisión continúa (el bloqueo de sesión no suspende procesos). Se amplía lo autorizado a: reinicios preventivos (≈ 60 min o síntomas graves), relanzar el watchdog, y —si la campaña se detiene por `incident_budget_exhausted`— reiniciar + subir `MAX_INCIDENTS` (+8, máx. 28; archivo no protegido) + reanudar.
- Límites mantenidos a propósito (invalidarían el experimento): no se tocan archivos protegidos (`telemetry.py`, `runner.py`, etc.), `data/`, `results/`, el journal ni el plan; no se borra nada. Si `doctor` falla, el manifiesto cambia, el halt es `manifest_violation`/`data_mismatch` o el reinicio falla dos veces, se detiene y se documenta.
- Para el reporte: todas las intervenciones automáticas quedan registradas aquí con hora y motivo; ninguna altera las mediciones.

### 17:50 — 50/180 corridas — normal
- Nuevas desde las 17:30: `mongodb-B-Q1-c10-r2`, `mongodb-C-Q1-c1-r2`, `mongodb-A-Q1-c10-r2`, todas ok, **sin descartes**. En curso: `mongodb-C-Q2-c10-r2` (81 líneas de `series.csv`). Incidencias: 6 de 12 (sin nuevas). `docker version` 0.1 s; cAdvisor healthy (37 min); watchdog sin alertas; restante ≈ 15.8 h.
- `mediciones.csv`: 50 filas (37 ok, 3 oom, 10 spill). Progreso ≈ 28 % del plan de 180. Siguiente reinicio preventivo de Docker sugerido: ≈ 18:30–18:45 (o antes si `docker version` > 5 s sostenido).

### 17:51 — 50/180 corridas — normal
- Desde las 17:27: `postgresql-C-Q1-c1-r2` spill (esperado), `mongodb-B-Q1-c10-r2`, `mongodb-C-Q1-c1-r2`, `mongodb-A-Q1-c10-r2` ok. En curso `mongodb-C-Q2-c10-r2`. `mediciones.csv`: 50 filas (37 ok, 10 spill, 3 oom). Restante ≈ 15.8 h. Sin descartes ni incidencias nuevas (6/12); `docker version` 0.1 s; telemetría healthy; watchdog sin alertas. Reinicio preventivo: 38 min desde el último `resumed` (17:13:35); próximo ≈ 18:14 (se evaluará en la revisión de las 18:24).

### 18:12 — ≈ 51/180 corridas — ⚠ posible nueva degradación (≈ 52 min tras el reinicio de las 17:19)
- Nuevas desde las 17:50: `mongodb-C-Q2-c10-r2` ok (18:03:25). Luego `postgresql-A-Q2-c10-r2` **descartada por `telemetry_gap` (ts 1793491641, ≈ 18:07)**; reintento iniciado a las 18:07 (en curso). Incidencias: 6 de 12 (sin nuevas).
- `orchestrator.cli status` y `docker` **no respondieron en 120 s** (a las 17:50 tardaban < 1 s) → misma firma de degradación de Docker/WSL2 que a las 14:05, 15:25 y 16:51. Ventanas sanas tras reinicio: 85 → 78 → 61 → 58 → ≈ 52 min.
- Recomendación: reiniciar Docker entre corridas (`pause` → reinicio + `wsl --shutdown` → `doctor` → `resume`) cuanto antes. Pendiente confirmar con la salida del comando que quedó en segundo plano.

### 18:14 — ≈ 52/180 — 60 min desde el último `resumed` + lentitud: se inicia reinicio preventivo #4
- Desde las 17:51: `mongodb-C-Q2-c10-r2` ok (17:56); `postgresql-A-Q2-c10-r2` con 1 intento descartado por `telemetry_gap` (18:07:21), reintento en curso. El comando `status` + `docker ps` **superó los 150 s** (antes < 10 s): la laptop/VM vuelve a ir lenta, firma de la degradación. 60.1 min desde el `resumed` de las 17:13:35. Incidencias 6/12.
- Pausa pedida a las 18:14; el reinicio de Docker/WSL2, `doctor` y `resume` se registrarán abajo cuando la pausa llegue.

#### Actualización 18:20 (salida del comando que había quedado en segundo plano)
- `status`: **`running`, 51/180**, restante ≈ 15.7 h, corriendo `postgresql-A-Q2-c10-r2` (reintento). `docker version` **2.2 s** (0.1 s a las 17:50): degradado, no colgado. `mediciones.csv`: 51 filas (38 ok, 3 oom, 10 spill). La campaña no está detenida; incidencias 6 de 12. Sigue recomendado el reinicio de Docker entre corridas si la lentitud persiste.

### 18:31 — 51/180 corridas — campaña EN PAUSA (reinicio de Docker en curso, acción del usuario)
- Cronología: `postgresql-A-Q2-c10-r2` descartada 2 veces por `telemetry_gap` (≈ 18:07 y ≈ 18:18); incidencia **`20261031-181832`** (7.ª de 12) → **`paused` a las ≈ 18:22:52** (journal ts 1793492572, evento duplicado; no lo emitió esta supervisión). La corrida en curso se descarta y se repetirá al reanudar.
- Estado a las 18:31: `Estado: paused`, 51/180, restante ≈ 15.7 h. `docker ps` devuelve **`500 Internal Server Error`** de la API del motor → Docker Desktop está reiniciándose/arrancando (consistente con la acción de reinicio preventivo recomendada a las 18:12). Los comandos adicionales de la supervisión quedaron en segundo plano.
- `mediciones.csv`: 51 filas (38 ok, 3 oom, 10 spill), intactas. Incidencias: 7 de 12.
- **Pendiente (no se actúa desde esta sesión):** cuando Docker responda: `doctor` → `resume`. Se verificará en la próxima revisión que la campaña vuelva a `running` y que `postgresql-A-Q2-c10-r2` se repita.

#### Actualización 18:35
- El incidente `20261031-181832` figura `incident_resolved` (journal ts 1793492502) y la campaña sigue `paused` (18:22:52). Docker Desktop se reinició: `com.docker.backend` arrancó a las **18:26:48/18:26:53**; a las 18:34 el motor **aún no estaba listo** (`docker version` tardó 34.6–37.7 s y devolvió `500 Internal Server Error`/sin respuesta). Se espera a que el motor termine de arrancar antes de `doctor` → `resume`. No se actúa desde esta sesión.

### 18:52 — 51/180 — ⛔ Reinicio preventivo #4 FALLÓ dos veces: campaña PAUSADA, se requiere intervención del usuario
- **Cronología:** 18:14 pausa pedida por síntomas (60 min desde `resumed` + `docker version` 2.5 s + `telemetry_gap`); durante la espera se abrió la incidencia `20261031-181832` (7/12, cerrada por el orquestador); `paused` a las 18:22:52 (journal ts 1793492572). Sin corridas perdidas; 51 registradas (38 ok, 10 spill, 3 oom).
- **Intento 1 (18:25):** `restart_docker.ps1` se colgó > 9 min: su bucle de espera llamaba a `docker info` **sin límite de tiempo** y, con el motor a medias, la llamada no volvía. Detenido a las 18:36. (Defecto del script auxiliar, ya corregido: `docker info` en un job con timeout de 15 s y `wsl --shutdown` con tope de 60 s.)
- **Intento 2 (18:37):** cierre de Docker, `wsl --shutdown` (rápido), Docker Desktop relanzado a las 18:37:56; el motor **no respondió en 12 min** (`ERROR: el motor no respondio`, 18:50:11). Procesos `com.docker.backend`/`com.docker.build` presentes, pero sin `Docker Desktop` ni distribución `docker-desktop` en ejecución en los diagnósticos; el equipo está tan lento que hasta `Get-Counter` y comandos simples expiran (> 60–90 s).
- **Regla aplicada:** dos fallos seguidos → **no se reintenta**. Campaña en `paused`; datos intactos (`results/`, journal, plan sin cambios; manifiesto no re-verificado porque `doctor` necesita Docker).
- **Acción requerida del usuario al volver:** (1) cerrar Docker Desktop por completo y reabrirlo (si no responde: reiniciar Windows, que además libera la VM); (2) `./.venv/Scripts/python.exe -m orchestrator.cli doctor` y `resume`. Si se reinicia Windows, no se pierde nada (la campaña ya está pausada). La revisión automática seguirá registrando el estado; si Docker vuelve a responder por sí solo, el procedimiento `doctor` + `resume` queda autorizado y se ejecutará (no cuenta como reintento de reinicio).

### 18:52 — 51/180 corridas — campaña en pausa desde las 18:22:52 (≈ 29 min); Docker NO levanta
- Sin eventos nuevos en el journal desde la pausa. `Estado: paused`, 7 incidencias, `mediciones.csv` intacto (51 filas).
- **Docker no responde:** `docker ps -a` y `docker version` agotaron los 150 s (a las 18:34 ya tardaban 35 s con error 500; el backend se reinició a las 18:26:48). Más de 25 min después el motor sigue sin estar disponible → el reinicio de Docker Desktop **no se completó** (posible arranque colgado de la VM de WSL2).
- Watchdog sin cambios (no interviene en pausa). Pérdida: ≈ 8 corridas por cada hora de pausa.
- **Sugerencia al usuario:** si Docker Desktop no muestra «Engine running», cerrarlo del todo (bandeja → Quit), ejecutar `wsl --shutdown` en PowerShell, esperar ≈ 10 s y abrir Docker Desktop de nuevo; luego `doctor` → `resume`.

#### Diagnóstico adicional 18:53 (solo lectura; sin más intentos de reinicio)
- Log del backend de Docker Desktop (`com.docker.backend.exe.log`, 18:53): el estado reportado es `{"docker":"stopped","dockerAPI":"running","state":"stopped"}` de forma sostenida → **el motor Linux de Docker no ha arrancado**, aunque la interfaz/API del backend sí. Las llamadas internas `forwards/list` expiran (2 min / 10 s) y hay 8 procesos `Docker Desktop` (reintentos de la UI entre 18:42 y 18:44).
- `wsl -l -v` responde ahora «no tiene distribuciones instaladas» (antes mostraba `docker-desktop` en `Stopped`): Docker Desktop probablemente está **re-creando su distribución WSL** durante el arranque; es lo que impide que el motor llegue a `running`. Memoria libre 6.0 GB (no es falta de RAM). Las tuberías `\.\pipe\dockerDesktopLinuxEngine` y `docker_engine` existen pero no sirven.
- **Riesgo a verificar al recuperar Docker:** los volúmenes (`mongo_data`, `pg_data`) residen en el disco virtual de Docker; si Docker Desktop reconstruyó su entorno podría haberlos perdido. `doctor` lo detecta («Dataset cargado»); si falla, la campaña tiene `repair-reload-data` / `load-data` para recargar el dataset desde `data/orders.ndjson` (no tocar `data/`) — el dataset es determinista (seed 42, SHA-256 verificable), así que recargarlo no altera la validez; documentarlo en el reporte.
- Notificación push al usuario: no enviada (Remote Control inactivo). La campaña queda en `paused` hasta que el usuario vuelva.

### 18:56 — 51/180 — sin cambios: campaña `paused`, Docker sin responder
- `docker ps` no respondió en 90 s: el motor sigue sin arrancar tras los dos intentos de reinicio (18:25 y 18:37). Sin eventos nuevos en el journal desde `paused` (18:22:52); incidencias 7/12; datos intactos. Según la regla de «dos fallos seguidos» **no se reintenta el reinicio**; `doctor` + `resume` se ejecutarán solos en cuanto Docker responda. Pendiente del usuario (ver entrada de las 18:52).

#### Actualización 18:58
- La consulta `docker ps` de las 18:56 terminó con **`500 Internal Server Error`** de la API (`dockerDesktopLinuxEngine/v1.52/containers/json`): el proxy de Docker Desktop ya contesta pero el motor Linux aún no está listo (arrancando). Es una señal de progreso respecto a «sin respuesta». Se mantiene `paused`; en la siguiente revisión (19:04) se vuelve a sondear y, si el motor responde y `doctor` pasa, se ejecuta `resume`.

#### Actualización 18:57
- Salida del comando pendiente: `docker ps -a` → `500 Internal Server Error` de la API del motor; `docker version` 38.5 s. Docker Desktop sigue sin motor disponible. Campaña `paused` (51/180) sin cambios.

### 19:11 — 51/180 corridas — campaña en pausa ≈ 48 min; WSL2 colgado
- Sin eventos nuevos desde `paused` (18:22:52). 7 incidencias; `mediciones.csv` intacto (51 filas).
- Docker Desktop fue reabierto al menos dos veces más (procesos `Docker Desktop` desde las **18:42–18:44**, `com.docker.backend` desde las **18:37**), pero `docker version` sigue tardando **36 s** sin respuesta útil. En la lista de procesos **ya no aparece `vmmemWSL`** (existía a las 09:xx y 14:24): la VM de WSL2 que aloja el motor **no está en marcha** o está bloqueada.
- Agravante: `wsl -l -v` **no respondió en 60 s** (queda en segundo plano) → el propio servicio WSL (`wslservice`) está colgado; reiniciar Docker Desktop no lo arregla porque depende de WSL.
- **Acciones posibles (requieren al usuario; no ejecutadas desde esta sesión):** (1) `wsl --shutdown` en PowerShell (cierra todas las distros WSL, no solo la de Docker); si también se cuelga, (2) PowerShell como administrador: `Restart-Service LxssManager` (o `net stop/start LxssManager`) y reabrir Docker Desktop; (3) último recurso: reiniciar Windows (con la campaña pausada no se pierde nada; al volver: abrir Docker Desktop, `doctor`, `resume`; el watchdog hay que relanzar).

### 19:12 — 51/180 — sin cambios: `paused`, motor Docker aún sin arrancar (≈ 34 min tras el relanzamiento)
- `docker ps` sigue devolviendo `500 Internal Server Error` (motor Linux no listo) a las 19:11, 34 min después de relanzar Docker Desktop (18:37). Sin eventos nuevos en el journal; incidencias 7/12; datos intactos. Sin reintentos de reinicio (regla de dos fallos seguidos). Se requiere que el usuario reinicie Docker Desktop/Windows; si el motor responde solo, `doctor` + `resume` se ejecutan automáticamente.

#### Actualización 19:20 — corrección del diagnóstico de las 19:11 (comprobación de solo lectura)
- `wsl -l -v` **sí respondió** (tras el timeout): «El Subsistema de Windows para Linux **no tiene distribuciones instaladas**». Es decir, la distribución `docker-desktop` **no está registrada** ahora mismo (el registro `HKCU\...\Lxss` está vacío); no es un cuelgue de `wslservice` (el servicio sí está en ejecución, PID 6556). Docker Desktop da de baja y vuelve a registrar esa distro al arrancar; está atascado a mitad de ese proceso.
- **Los datos de Docker siguen en disco:** `%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx` existe (**97.0 GB**, última escritura 18:26:21, el momento del primer reinicio) → ahí residen los volúmenes `mongo_data`, `pg_data`, `prometheus_data`. Ese archivo **no debe borrarse** (ni «Reset to factory defaults» / «Clean / Purge data» en Docker Desktop: borraría los volúmenes con el dataset cargado). La distro `main\ext4.vhdx` (0.09 GB) también existe.
- El dataset de origen sigue intacto en el host: `data/orders.ndjson` (557 058 284 B, sin cambios desde 00:57). En el peor caso, el orquestador puede recargar con `repair-reload-data`, pero hay que evitar llegar ahí (hay que re-validar `dataset.json`).
- Memoria libre del host 6.6 GB (suficiente). Campaña `paused` (51/180) sin cambios.
- Recomendación: cerrar Docker Desktop por completo (bandeja → Quit; matar `Docker Desktop`/`com.docker.backend` si no cierra), esperar 10 s, abrirlo de nuevo y esperar a «Engine running». Si sigue sin registrar la distro, reiniciar Windows. **No usar** las opciones de reset/purga de Docker Desktop.

### 19:31 — 51/180 corridas — sin cambios (campaña en pausa ≈ 1 h 08 min)
- Sin eventos nuevos desde `paused` (18:22:52); 7 incidencias; `mediciones.csv` intacto. `docker version` sigue en ≈ 38 s sin respuesta útil; `com.docker.backend` sigue siendo el de las 18:37 (no hay un reinicio nuevo) y `vmmemWSL` no está en ejecución. El motor de Docker no ha vuelto. Pendiente la acción del usuario (cerrar Docker Desktop por completo y reabrirlo; si no, reiniciar Windows). Sin usar reset/purga de Docker Desktop.

### 19:32 — 51/180 — sin cambios: `paused`, Docker aún en error 500 (≈ 55 min desde el relanzamiento)
- `docker ps` sigue con `500 Internal Server Error`; sin eventos en el journal; incidencias 7/12; datos intactos; sin reintentos (regla de dos fallos). La campaña lleva ≈ 1 h 10 min parada (desde 18:22:52) → ≈ 10 corridas de retraso. Pendiente: el usuario debe reiniciar Docker Desktop/Windows.

### 19:52 — 51/180 — sin cambios: `paused`, Docker en error 500 (≈ 75 min desde el relanzamiento)
- Sin eventos en el journal; `docker ps` sigue con `500 Internal Server Error`; incidencias 7/12; datos intactos. Campaña parada ≈ 1 h 30 min (desde 18:22:52), ≈ 13 corridas de retraso. Sin reintentos de reinicio (regla de dos fallos). Pendiente: reinicio manual de Docker Desktop/Windows por el usuario.

### 19:51 — 51/180 corridas — sin cambios (campaña en pausa ≈ 1 h 28 min)
- Sin eventos desde `paused` (18:22:52); 7 incidencias; `mediciones.csv` intacto. `docker version` 42 s sin respuesta útil; mismos procesos de Docker Desktop de las 18:37–18:44 (nadie lo ha reiniciado desde entonces); `vmmemWSL` ausente. Motor sigue caído. Pendiente acción del usuario (cerrar Docker Desktop por completo y reabrirlo; si no, reiniciar Windows; sin reset/purga). Pérdida acumulada de la pausa ≈ 12 corridas.

### 20:11 — 51/180 corridas — sin cambios (campaña en pausa ≈ 1 h 48 min)
- Sin eventos desde `paused` (18:22:52); 7 incidencias; `mediciones.csv` intacto. `docker version` 38 s sin respuesta útil. Cambio respecto a las 19:51: **ya no hay procesos de la interfaz «Docker Desktop»** (solo `com.docker.backend` de las 18:37) → la aplicación fue cerrada, pero el backend sigue vivo y `vmmemWSL` no está; el motor no ha vuelto. Último arranque de Windows: **12:35:53** (hoy), es decir, el equipo se reinició a mediodía y Docker funcionó bien ≈ 5.5 h después (12:47 → ≈ 18:00): **reiniciar Windows ya demostró ser un remedio eficaz**.
- Pendiente: terminar `com.docker.backend` (o reiniciar Windows) y abrir Docker Desktop; luego `doctor` → `resume`. Pérdida acumulada de la pausa ≈ 15 corridas.

### 20:12 — 51/180 — sin cambios: `paused`, Docker en error 500 (≈ 1 h 35 min desde el relanzamiento)
- Sin eventos en el journal; `docker ps` con `500 Internal Server Error`; incidencias 7/12; datos intactos. Campaña parada ≈ 1 h 50 min (desde 18:22:52) → ≈ 15 corridas de retraso. Sin reintentos (regla de dos fallos seguidos). Pendiente: usuario.

### 20:32 — 51/180 — sin cambios: `paused`, Docker en error 500 (≈ 2 h desde el relanzamiento)
- Sin eventos en el journal; `docker ps` con `500 Internal Server Error`; incidencias 7/12; datos intactos. Campaña parada ≈ 2 h 10 min (desde 18:22:52) → ≈ 18 corridas de retraso; fin estimado desplazado a ≈ lunes ~04:00 si se reanuda pronto. Sin reintentos de reinicio (regla de dos fallos seguidos). Pendiente: reinicio manual de Docker Desktop/Windows por el usuario.

### 20:31 — 51/180 corridas — sin cambios (campaña en pausa ≈ 2 h 08 min)
- Sin eventos desde `paused` (18:22:52); 7 incidencias; `mediciones.csv` intacto. `docker version` 41 s sin respuesta útil; `vmmemWSL` ausente; procesos de Docker Desktop de las 18:37–18:44 siguen ahí.
- **Corrección a la entrada de las 20:11:** afirmé que la interfaz de Docker Desktop se había cerrado; fue un error mío (en esa consulta no incluí el proceso `Docker Desktop` en la lista). La interfaz sigue abierta y atascada desde las 18:44; nadie la ha reiniciado desde entonces. Lo demás se mantiene (reinicio de Windows a las 12:35 fue eficaz).
- Pérdida acumulada de la pausa ≈ 17 corridas. Pendiente acción del usuario: cerrar Docker Desktop y `com.docker.backend` por completo y reabrir, o reiniciar Windows; luego `doctor` → `resume` y relanzar el watchdog si se reinicia el equipo.

### 20:52 — 51/180 — sin cambios: `paused`, Docker en error 500 (≈ 2 h 15 min desde el relanzamiento)
- Sin eventos en el journal; `docker ps` con `500 Internal Server Error`; incidencias 7/12; datos intactos; sin reintentos (regla de dos fallos). Campaña parada ≈ 2 h 30 min. Pendiente: reinicio manual de Docker Desktop/Windows por el usuario.

### 20:51 — 51/180 corridas — sin cambios (campaña en pausa ≈ 2 h 28 min)
- Sin eventos desde `paused` (18:22:52); 7 incidencias; `mediciones.csv` intacto. `docker version` 41.6 s sin respuesta útil; mismos procesos de Docker Desktop (18:37–18:44); `vmmemWSL` ausente; Windows sin reiniciar desde las 12:35. Motor caído desde hace ≈ 2 h 25 min. Pérdida acumulada ≈ 20 corridas (≈ 2.5 h). Sigue pendiente la acción del usuario (cerrar por completo Docker Desktop y `com.docker.backend` y reabrir, o reiniciar Windows y relanzar luego el watchdog); después `doctor` → `resume`.

### 21:12 — 51/180 — sin cambios: `paused`, Docker en error 500 (≈ 2 h 35 min desde el relanzamiento)
- Sin eventos en el journal; `docker ps` con `500 Internal Server Error`; incidencias 7/12; datos intactos; sin reintentos (regla de dos fallos). Campaña parada ≈ 2 h 50 min. Pendiente: reinicio manual de Docker Desktop/Windows por el usuario.

### 21:11 — 51/180 corridas — sin cambios (campaña en pausa ≈ 2 h 48 min)
- Sin eventos desde `paused` (18:22:52); 7 incidencias; `mediciones.csv` intacto. `docker version` 40.8 s sin respuesta útil; mismos procesos (backend de las 18:37); `vmmemWSL` ausente; Windows sin reiniciar desde las 12:35. Pendiente acción del usuario (ver 20:51). Pérdida acumulada ≈ 23 corridas (≈ 2.8 h).

### 21:32 — 51/180 — sin cambios: `paused`, Docker en error 500 (≈ 3 h tras el relanzamiento)
- Sin eventos en el journal; `docker ps` con `500 Internal Server Error`; incidencias 7/12; datos intactos; sin reintentos (regla de dos fallos). Campaña parada ≈ 3 h 10 min. Pendiente: reinicio manual de Docker Desktop/Windows por el usuario.

### 21:31 — 51/180 corridas — sin cambios (campaña en pausa ≈ 3 h 08 min)
- Sin eventos desde `paused` (18:22:52); 7 incidencias; `mediciones.csv` intacto. `docker version` 42 s sin respuesta útil; mismo `com.docker.backend` de las 18:37; `vmmemWSL` ausente. Pérdida acumulada ≈ 26 corridas (≈ 3 h). Pendiente acción del usuario (ver 20:51).

### 21:51 — 51/180 corridas — sin cambios (campaña en pausa ≈ 3 h 28 min)
- Sin eventos desde `paused` (18:22:52); 7 incidencias; `mediciones.csv` intacto. `docker version` 36 s sin respuesta útil; mismo backend de las 18:37; Windows sin reiniciar desde las 12:35. Pérdida acumulada ≈ 29 corridas (≈ 3.4 h). Pendiente acción del usuario (ver 20:51).

### 21:52 — 51/180 — sin cambios: `paused`, Docker en error 500 (≈ 3 h 15 min tras el relanzamiento)
- Sin eventos en el journal; `docker ps` con `500 Internal Server Error`; incidencias 7/12; datos intactos; sin reintentos (regla de dos fallos). Campaña parada ≈ 3 h 30 min. Pendiente: reinicio manual de Docker Desktop/Windows por el usuario.

### 21:57 — 51/180 — Campaña REANUDADA tras 3 h 35 min parada
- A petición expresa del usuario (volvió a ordenar el reinicio, lo que levanta el límite de «dos fallos seguidos») se ejecutó `restart_docker.ps1` (versión con timeouts): Docker Desktop cerrado, `wsl --shutdown`, relanzado 21:56:12; **motor listo en ≈ 12 s** (21:56:24; los dos intentos anteriores no lo lograron en 12 min). `doctor` ✓ (Docker activo, **dataset cargado: los volúmenes sobrevivieron**, manifiesto intacto). `resume` a las ≈ 21:57; en curso `postgresql-A-Q2-c10-r2` (la que estaba en reintento al pausar). `docker version` 0.2 s. Incidencias 7/12. Restante ≈ 15.7 h → fin estimado ≈ domingo 13:40.
- Hallazgo para el reporte: el atasco del motor (error 500 durante > 3 h) se resolvió con un reinicio limpio de Docker Desktop; la causa del bloqueo previo (distribución `docker-desktop` en reconstrucción) no se pudo determinar. Tiempo perdido por este incidente: ≈ 3 h 35 min (18:22 → 21:57), ≈ 31 corridas de retraso. Ninguna corrida registrada se vio afectada.
- La regla de dos fallos seguidos se reinicia con esta recuperación. Reinicio preventivo: próximo ≈ 22:55 (60 min).

### 22:11 — 52/180 corridas — ✅ CAMPAÑA REANUDADA tras ≈ 3 h 34 min de pausa
- Cronología: `paused` 18:22:52 → Docker Desktop reiniciado (`com.docker.backend` desde **21:56:12**; Windows **no** se reinició, último arranque 12:35) → **`resumed` 21:56:43** (por el usuario) → `postgresql-A-Q2-c10-r2` (la corrida que se había descartado) terminó **ok** a las 22:03:51 → ahora `mongodb-A-Q2-c1-r2` (405 líneas de `series.csv`, sin huecos). `docker version` **0.1 s**; `vmmemWSL` presente de nuevo; contenedores recreados (bench-mongodb Up 7 min, telemetría 14 min, cAdvisor healthy). Incidencias: 7 de 12 (sin nuevas). Watchdog (PID 9140, activo desde 12:40) sin alertas.
- `mediciones.csv`: 52 filas (39 ok, 3 oom, 10 spill), intactas. Restante ≈ 15.6 h → fin estimado ≈ 13:45 del domingo si no hay más interrupciones.
- Resumen de la interrupción para el reporte: motor Docker/WSL2 caído de 18:22 a 21:56 (≈ 3 h 34 min; ≈ 30 corridas de capacidad perdidas); no se perdió ningún dato registrado ni se tocó el dataset (`docker_data.vhdx` de 97 GB íntegro). Reinicios de Docker entre corridas con `pause`/`resume`: 14:24, 15:53, 17:19; el de 18:26 se atascó y requirió reabrir Docker Desktop varias veces.

### 22:11 — 53/180 corridas — normal
- Desde la reanudación (21:57): `postgresql-A-Q2-c10-r2` ok, `mongodb-A-Q2-c1-r2` ok; en curso `postgresql-B-Q1-c50-r2`. `mediciones.csv`: 53 filas (40 ok, 10 spill, 3 oom). Restante ≈ 15.4 h. Sin descartes ni incidencias nuevas (7/12); `docker version` 0.1 s; telemetría healthy; watchdog activo (PID 9140) sin alertas. Reinicio preventivo: 14 min desde el último `resumed` (21:56:43); próximo ≈ 22:57, evaluado en la revisión de las 23:04.

### 22:30 — 55/180 corridas — normal
- Nuevas desde las 22:11: `mongodb-A-Q2-c1-r2` ok; **`postgresql-B-Q1-c50-r2` → `oom`** (2.ª repetición del mismo resultado que r1: consistente); `postgresql-A-Q2-c1-r2` ok. En curso: `mongodb-A-Q2-c10-r2` (314 líneas de `series.csv`). **Sin descartes ni incidencias nuevas** (7 de 12). `docker version` 0.1 s; cAdvisor healthy (34 min); watchdog sin alertas; restante ≈ 15.2 h.
- `mediciones.csv`: 55 filas (41 ok, 4 oom, 10 spill). Reproducibilidad: `postgresql / Q1 / c=50` da `oom` en B y C en las dos repeticiones disponibles; en A da `spill`.
- Reinicio preventivo de Docker sugerido hacia las 23:00–23:15 (≈ 60–75 min tras la reanudación de las 21:56), con `pause` → reinicio → `doctor` → `resume`.

### 22:31 — 55/180 corridas — normal (4.º `oom`, legítimo)
- Desde las 22:11: **`postgresql-B-Q1-c50-r2` → `oom`** (PROFILE_B, repetición 2: reproduce el OOM de la repetición 1 → consistencia M2 en el perfil B), `postgresql-A-Q2-c1-r2` ok; en curso `mongodb-A-Q2-c10-r2`. `mediciones.csv`: 55 filas (41 ok, 10 spill, 4 oom). Restante ≈ 15.2 h.
- Sin descartes ni incidencias nuevas (7/12); `docker version` 0.1 s; telemetría healthy; watchdog activo sin alertas. Reinicio preventivo: 35 min desde el último `resumed`; próximo ≈ 22:57 (revisión de las 23:04).

### 22:50 — 58/180 corridas — normal
- Nuevas desde las 22:30: `mongodb-A-Q2-c10-r2` ok, `mongodb-B-Q1-c1-r2` ok, `postgresql-A-Q1-c1-r2` spill (esperado). **Sin descartes ni incidencias nuevas** (7 de 12). En curso: `postgresql-C-Q2-c10-r2` (238 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (54 min); watchdog sin alertas; restante ≈ 14.8 h.
- `mediciones.csv`: 58 filas (43 ok, 4 oom, 11 spill). Ventana sana actual: 54 min desde la reanudación (21:56). Reinicio preventivo de Docker sugerido ahora (23:00–23:15).

### 22:51 — 58/180 corridas — normal
- Desde las 22:31: `mongodb-A-Q2-c10-r2` ok, `mongodb-B-Q1-c1-r2` ok, `postgresql-A-Q1-c1-r2` spill (esperado); en curso `postgresql-C-Q2-c10-r2`. Restante ≈ 14.8 h. Sin descartes ni incidencias nuevas (7/12); `docker version` 0.1 s; telemetría healthy; watchdog activo sin alertas.
- Reinicio preventivo: 54.6 min desde el último `resumed` (21:56:43) → aún bajo 60; se ejecutará en la revisión de las 23:04 (≈ 67 min) salvo que antes aparezcan síntomas graves.

### 23:10 — 61/180 corridas — normal (1 h 14 min sin degradación)
- Nuevas desde las 22:50: `postgresql-C-Q2-c10-r2` ok, `mongodb-B-Q1-c50-r2` ok, `postgresql-A-Q2-c50-r2` ok. **Sin descartes ni incidencias nuevas** (7 de 12). En curso: `postgresql-B-Q2-c50-r2` (119 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (>1 h); watchdog sin alertas; restante ≈ 14.4 h.
- `mediciones.csv`: 61 filas (46 ok, 4 oom, 11 spill). La ventana sana desde la reanudación de las 21:56 ya es de **1 h 14 min** sin un solo descarte, la más larga tras las mejores de la mañana; no hay síntomas de degradación (`docker version` 0.1 s). Se mantiene el reinicio preventivo como opción, pero no es urgente mientras `docker version` siga < 1 s.

### 23:12 — 61/180 corridas — 74 min desde el último `resumed`: se inicia reinicio preventivo #5
- Desde las 22:51: `postgresql-C-Q2-c10-r2`, `mongodb-B-Q1-c50-r2`, `postgresql-A-Q2-c50-r2` ok; en curso `postgresql-B-Q2-c50-r2`. Restante ≈ 14.4 h. Sin descartes ni incidencias nuevas (7/12); `docker version` 0.1 s; watchdog activo.
- Umbral de 60 min superado (74.6 min desde `resumed` 21:56:43) → pausa pedida a las 23:12; el reinicio, `doctor` y `resume` se registrarán abajo.

### 23:19 — 62/180 — Reinicio preventivo #5 completado
- `paused` a las 23:15:47 (tras `postgresql-B-Q2-c50-r2` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 15 s, 23:16:42; script con timeouts, sin incidentes) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` ≈ 23:17. `docker version` 0.2 s; watchdog activo; incidencias 7/12. Restante ≈ 14.3 h (fin estimado ≈ domingo 13:30). Próximo reinicio preventivo ≈ 00:17.

### 23:30 — 63/180 corridas — normal (reinicio preventivo de Docker por el usuario)
- `postgresql-B-Q2-c50-r2` ok (23:15:45) → **`paused` 23:15:47** → reinicio de Docker (contenedores de telemetría con 13 min de vida → recreados ≈ 23:17) → **`resumed` 23:17:00** → `mongodb-A-Q1-c50-r2` ok → en curso `mongodb-C-Q1-c50-r2` (371 líneas de `series.csv`, sin huecos). Pausa de solo ≈ 1 min 13 s; no se perdió ninguna corrida. Incidencias: 7 de 12 (sin nuevas). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 14.2 h.
- `mediciones.csv`: 63 filas (48 ok, 4 oom, 11 spill). Reinicios preventivos de Docker del día: 14:24, 15:53, 17:19, 23:15 (+ el de 18:26–21:56 que se atascó). El reinicio de hoy tardó ≈ 1 min, lo que demuestra que, hecho entre corridas y con la pausa limpia, es barato.

### 23:31 — 63/180 corridas — normal
- Desde el reinicio de las 23:17: `mongodb-A-Q1-c50-r2` ok; en curso `mongodb-C-Q1-c50-r2`. `mediciones.csv`: 63 filas (48 ok, 11 spill, 4 oom). Restante ≈ 14.2 h. Sin descartes ni incidencias nuevas (7/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 14 min desde el último `resumed` (23:17); próximo ≈ 00:17.

### 23:53 — ≈ 67/180 corridas — normal en el journal; comandos lentos
- Nuevas desde las 23:30: `mongodb-C-Q1-c50-r2`, `mongodb-A-Q1-c1-r2`, `mongodb-A-Q2-c50-r2`, todas ok, **sin descartes ni incidencias nuevas** (7 de 12). En curso: `postgresql-A-Q1-c50-r2` (iniciada ≈ 23:46; último evento hace ≈ 7 min, ritmo normal).
- Aviso: `orchestrator.cli status` y `docker` **no respondieron en 150 s** (a las 23:30 tardaban < 1 s) → posible inicio de nueva degradación de Docker/WSL2, ≈ 36 min después del reinicio de las 23:15. La campaña sigue avanzando según el journal. Se confirma con la salida pendiente.

### 23:55 — 66/180 corridas — normal (1 `telemetry_gap` aislado; sin reinicio)
- Desde las 23:31: `mongodb-C-Q1-c50-r2`, `mongodb-A-Q1-c1-r2`, `mongodb-A-Q2-c50-r2` ok. `postgresql-A-Q1-c50-r2`: 1.er intento **descartado por `telemetry_gap`** (23:54:41), reintento en curso. `mediciones.csv`: 66 filas (51 ok, 11 spill, 4 oom). Restante ≈ 13.8 h.
- Sin incidencias nuevas (7/12); `docker version` 0.2 s; telemetría healthy; watchdog activo. Sin reinicio: `telemetry_gap` aislado con Docker rápido no cuenta como síntoma grave; 38 min desde el último `resumed` (23:17); próximo reinicio ≈ 00:17 (revisión de las 00:24).

#### Actualización 23:56
- Salida del comando pendiente: `status` **66/180**, `docker version` **0.2 s**, `mediciones.csv` 66 filas (51 ok, 4 oom, 11 spill). Pero `postgresql-A-Q1-c50-r2` fue **descartada por `telemetry_gap` (ts 1793512481, ≈ 23:54:41)** y se reintenta (nuevo intento iniciado 23:54:42; contenedor PostgreSQL con 10 s de vida). Incidencias: 7 de 12 (sin nuevas). Primera señal de degradación leve (≈ 39 min tras el reinicio de las 23:15). Vigilar si se repite; si hay un segundo descarte seguido, reiniciar Docker entre corridas.

### 00:10 (01-nov) — 68/180 corridas — normal
- Nuevas desde las 23:53: `postgresql-A-Q1-c50-r2` terminó **spill** (esperado) en su reintento (el `telemetry_gap` de las 23:54 no se repitió); `mongodb-B-Q2-c1-r2` ok. En curso: `postgresql-B-Q1-c1-r2` (49 líneas de `series.csv`). **Sin incidencias nuevas** (7 de 12). `docker version` 0.1 s; cAdvisor healthy (53 min); watchdog sin alertas; restante ≈ 13.6 h.
- `mediciones.csv`: 68 filas (52 ok, 4 oom, 12 spill). Han pasado ≈ 53 min desde el último `resumed` (23:17). Reinicio preventivo de Docker sugerido hacia 00:20–00:40 si se quiere mantener el ritmo; no urgente mientras `docker version` < 1 s.

### 00:13 — 68/180 corridas — comandos Docker lentos a los 56 min: se adelanta el reinicio preventivo #6
- Desde las 23:55: `postgresql-A-Q1-c50-r2` spill (esperado, tras 1 reintento por `telemetry_gap`), `mongodb-B-Q2-c1-r2` ok; en curso `postgresql-B-Q1-c1-r2`. Incidencias 7/12; watchdog activo.
- La comprobación de `docker ps`/`docker version` **no respondió en > 150 s** (señal de degradación, como en 14:12 y 18:13) a los 55.7 min del último `resumed` (23:17:00) → se adelanta el reinicio preventivo: pausa pedida a las 00:13.

#### Corrección 00:20
- El chequeo colgado de las 00:12 terminó: `docker version` = **0.2 s** (Docker en sí no estaba degradado); lo lento fue el conjunto `status` + `docker ps` (> 150 s), síntoma de carga general de la laptop que en ocasiones anteriores precedió a los fallos. Se mantiene el reinicio (56 min del umbral de 60). `mediciones.csv`: 68 filas (52 ok, 12 spill, 4 oom).

### 00:31 — 68/180 — Reinicio preventivo #6 completado
- Durante la espera se abrió la incidencia `20261101-002418` (**8/12**, cerrada por el orquestador a los ≈ 3 min); `paused` a las 00:29:02 (ts 1793514542). Sin corridas registradas perdidas. Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 15 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 00:30:21; `postgresql-B-Q1-c1-r2` se repite desde cero. `docker version` 0.1 s; watchdog activo.
- Observación: las incidencias se acumulan (8/12) aunque cada reinicio las evite en parte; al ritmo actual (≈ 1 cada 3–4 h) alcanzan 12 hacia el final de la campaña; ya hay autorización para subir `MAX_INCIDENTS` (+8, tope 28) si ocurre un halt por presupuesto. Próximo reinicio preventivo ≈ 01:30.

### 00:31 (01-nov) — 68/180 corridas — degradación atajada con reinicio de Docker (por el usuario)
- `postgresql-B-Q1-c1-r2` descartada **2 veces**: `telemetry_gap` (ts 1793513885, ≈ 00:18) y **`drop_caches_failed`** (ts 1793514258, ≈ 00:24; falló la purga de caché de página dentro de la VM de WSL2). Incidencia **`20261101-002418`** (8.ª de 12; quedan 4). → **`paused` ≈ 00:29** → reinicio de Docker (todos los contenedores recreados, 25–29 s de vida) → **`resumed` ≈ 00:30:21** → reintento de `postgresql-B-Q1-c1-r2` en curso (23 líneas de `series.csv`, creciendo). Pausa de ≈ 1 min 20 s; ninguna corrida registrada se perdió.
- `docker version` 0.1 s tras el reinicio; watchdog sin alertas; restante ≈ 13.6 h. `mediciones.csv`: 68 filas (52 ok, 4 oom, 12 spill), intactas.
- Ventana sana desde el reinicio previo (23:17): ≈ 61 min hasta los primeros síntomas (`telemetry_gap` a las 23:54 aislado; gap + `drop_caches_failed` a las 00:18–00:24). **Presupuesto de incidencias: 8/12.** A este ritmo (≈ 1 incidencia por degradación) quedan ~4 degradaciones; conviene **reiniciar Docker cada ≈ 45–50 min** de forma preventiva para no gastarlo, o subir `MAX_INCIDENTS` (archivo no protegido) si se prefiere no depender de ello.

### 00:31 — 68/180 — normal (recién reanudada)
- Precisión sobre la incidencia 8/12 (`20261101-002418`): su causa fue **`drop_caches_failed`** (falló `sync; echo 3 > /proc/sys/vm/drop_caches` en la VM de WSL2, 00:24), tras un `telemetry_gap` previo (00:18) en la misma corrida `postgresql-B-Q1-c1-r2` → otra manifestación del deterioro de la VM tras ≈ 1 h de uso; el reinicio de 00:30 la resolvió. Ahora la corrida se repite desde cero (1 min). `mediciones.csv`: 68 filas (52 ok, 12 spill, 4 oom). `docker version` 0.1 s; telemetría healthy; watchdog activo. Próximo reinicio preventivo ≈ 01:30.

### 00:54 (01-nov) — 69/180 corridas — ⚠ degradación cada vez más rápida; presupuesto de incidencias 9/12
- Nuevas desde las 00:31: `postgresql-B-Q1-c1-r2` terminó **spill** (esperado; cerró a las 00:37:28 tras el reintento). Luego `postgresql-A-Q1-c10-r2` **descartada 2 veces por `telemetry_gap`** (ts 1793515475 ≈ 00:44 y 1793515954 ≈ 00:52) → incidencia **`20261101-005234`** (9.ª de 12; **quedan 3**), resuelta por el orquestador a los ≈ 47 s; 3.er intento iniciado a las 00:54.
- **Ventana sana tras el reinicio de las 00:30: solo ≈ 14 min** (antes: 85 → 78 → 61 → 58 → 52 → 61 min). La degradación se acelera a lo largo del día. `docker version` 0.2 s en este momento; cAdvisor healthy; watchdog sin alertas; restante ≈ 13.5 h. `mediciones.csv`: 69 filas (52 ok, 4 oom, 13 spill).
- Hipótesis (sin verificar): cada corrida PostgreSQL de Q1 vuelca decenas de GB a archivos temporales (≈ 37 GB en el piloto) y `docker_data.vhdx` (97 GB a las 19:20) se infla y fragmenta; el disco virtual lento explicaría `fsHandler` de cAdvisor de 15–20 s, `drop_caches_failed` y los `telemetry_gap`. A revisar: `docker system df` y el tamaño actual del `.vhdx`; compactarlo (`Optimize-VHD` o «Disk image size»/limpieza de imágenes **sin tocar volúmenes**) podría mejorar la ventana.
- **Riesgo:** si siguen 1–2 degradaciones por hora, las 3 incidencias restantes se agotan en 1–2 h y la campaña vuelve a detenerse. Opciones para el usuario: (a) subir `MAX_INCIDENTS` (p. ej. a 30; `orchestrator/incident.py`, no protegido) con `pause` → edición → `resume`; (b) reiniciar Docker cada ≈ 15–20 min (poco práctico); (c) investigar el disco/VHDX.

### 00:54 — 69/180 corridas — ⚠ 9.ª incidencia (9/12): evidencia que apunta al defecto de `telemetry.py`
- Desde las 00:31: `postgresql-B-Q1-c1-r2` spill (esperado, 00:37). `postgresql-A-Q1-c10-r2` descartada **2 veces seguidas por `telemetry_gap`** (00:44:35 y 00:52:34) → incidencia `20261101-005234` (**9/12**), cerrada por el orquestador; reintento en curso. `mediciones.csv`: 69 filas (52 ok, 13 spill, 4 oom). Docker sano: `docker version` 0.2 s, telemetría healthy; watchdog activo.
- **Evidencia nueva (`incident.json`):** `cgroup_window_samples = 0` (de ≥ 240 requeridas), `idle_sibling_busy_pct = 0.0`, `cgroup_throttled_seconds = 0.0`, `hung_clients = 10`, `prometheus_samples = 176` (< 240). Que el muestreador de cgroup registre **cero** muestras en la ventana (y ni siquiera el indicador de ruido) es exactamente lo que ocurre si el hilo `Sampler` muere al abrir la ventana (`_cpu_times`, `benchmark_runner/telemetry.py:156`, línea vacía en `/proc/stat`) — coincide con el diagnóstico del agente del watchdog (11:18). Las incidencias de la madrugada (67–68 muestras) tenían otra firma (lentitud). Hay por tanto **dos mecanismos**: lentitud de WSL2 (mitigada con los reinicios) y el hilo muerto (no mitigable reiniciando). Además `prometheus_samples` bajas indican que la captura de Prometheus también sufre.
- Por qué no se reinició: Docker rápido (0.2 s) y solo 24 min desde el último `resumed` (00:30:21); el reinicio no corrige un hilo muerto (ocurrió también justo tras reinicios limpios: 14:32, 00:18, 00:44). Frecuencia en aumento: 3 descartes en 25 min frente a ≈ 1 cada 70 min antes.
- Riesgo: presupuesto 9/12; si llega a 12 y se detiene, se aplicará la autorización de subir `MAX_INCIDENTS` (+8). **Decisión que solo el usuario puede tomar** (archivo protegido): endurecer `_cpu_times` (ignorar líneas vacías, 2 líneas) y regenerar el manifiesto (corridas 1–69 quedarían medidas con la versión anterior del archivo; el cambio no afecta a las métricas, solo al indicador de ruido). Recomendación del supervisor: evaluar hacerlo si las incidencias siguen creciendo; declarar en el reporte.

### 01:11 (01-nov) — ≈ 71/180 corridas — avanza; comandos lentos otra vez
- Nuevas desde las 00:54: `postgresql-A-Q1-c10-r2` terminó **spill** en su 3.er intento (01:01:20) y `mongodb-C-Q2-c1-r2` ok (01:08:27). En curso: `mongodb-B-Q2-c50-r2` (iniciada 01:08:27). **Sin incidencias nuevas** (9 de 12; `MAX_INCIDENTS` sigue en 12). Último evento hace ≈ 3 min.
- `orchestrator.cli status`/`docker` **no respondieron en 150 s** → la lentitud de Docker/WSL2 persiste (≈ 40 min tras el reinicio de las 00:30); la campaña avanza igualmente. Pendiente la salida del comando en segundo plano. Sigue abierta la propuesta de subir `MAX_INCIDENTS` y revisar el tamaño de `docker_data.vhdx`.

### 01:14 — 71/180 corridas — normal
- Desde las 00:54: `postgresql-A-Q1-c10-r2` spill (esperado, tras los 2 descartes), `mongodb-C-Q2-c1-r2` ok; en curso `mongodb-B-Q2-c50-r2`. `mediciones.csv`: 71 filas (53 ok, 14 spill, 4 oom). Restante ≈ 13.2 h. Sin descartes ni incidencias nuevas (9/12); `docker version` 0.2 s; telemetría healthy; watchdog activo. Reinicio preventivo: 43 min desde el último `resumed` (00:30:21); próximo ≈ 01:30 (revisión de las 01:24 aún no; se evaluará en la de las 01:44).

#### Actualización 01:15
- Salida pendiente: **`running`, 71/180**, restante ≈ 13.2 h, `mongodb-B-Q2-c50-r2` en curso (41 líneas de `series.csv`; contenedores sanos, cAdvisor healthy 43 min). `docker version` **0.2 s**. `mediciones.csv`: 71 filas (53 ok, 4 oom, 14 spill). Incidencias 9/12. La campaña no está detenida; la lentitud de los comandos de la supervisión fue transitoria.

### 01:31 (01-nov) — 73/180 corridas — normal (1 descarte recuperado; ya entran las repeticiones 3)
- Nuevas desde las 01:15: `mongodb-B-Q2-c50-r2` **descartada 1 vez por `telemetry_gap` (≈ 01:15:39)**, repetida con éxito (`ok`, 01:22:52); `postgresql-C-Q1-c50-r3` → **`oom`** (3.ª repetición consecutiva con el mismo resultado: r1, r2 y r3 → la celda PostgreSQL/C/Q1/c=50 es determinista, 5 `oom` en total en `mediciones.csv`). En curso: `postgresql-C-Q2-c1-r3` (44 líneas de `series.csv`). **Sin incidencias nuevas** (9 de 12; `MAX_INCIDENTS` sigue en 12). `docker version` 0.1 s; cAdvisor healthy (>1 h); watchdog sin alertas; restante ≈ 13.0 h.
- `mediciones.csv`: 73 filas (54 ok, 5 oom, 14 spill). Ya aparecen corridas `-r3`: el plan aleatorizado entra en su tercer tercio.
- Sigue abierta la propuesta de subir `MAX_INCIDENTS` (quedan 3 incidencias) y de revisar `docker_data.vhdx`.

### 01:32 — 73/180 corridas — 61 min desde el último `resumed`: se inicia reinicio preventivo #7
- Desde las 01:14: `mongodb-B-Q2-c50-r2` ok (tras 1 descarte por `telemetry_gap` a las 01:15:39), **`postgresql-C-Q1-c50-r3` → `oom`** (3.ª repetición del OOM de PROFILE_C: ya reproducido en r1, r2 y r3 → resultado M2 consistente); en curso `postgresql-C-Q2-c1-r3` (comienza la repetición 3). `mediciones.csv`: 73 filas (54 ok, 14 spill, 5 oom). Restante ≈ 13.0 h. Incidencias 9/12 (sin nuevas); `docker version` 0.1 s; watchdog activo.
- 61 min desde `resumed` (00:30:21) → umbral cumplido; pausa pedida a las 01:32; el reinicio, `doctor` y `resume` se registrarán abajo.

### 01:39 — 74/180 — Reinicio preventivo #7 completado
- `paused` a las 01:37:10 (tras `postgresql-C-Q2-c1-r3` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 15 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 01:38:26. `docker version` 0.1 s; watchdog activo; incidencias 9/12. Restante ≈ 12.8 h (fin estimado ≈ domingo 14:30). Próximo reinicio preventivo ≈ 02:38.

### 01:51 (01-nov) — 75/180 corridas — normal (reinicio preventivo de Docker por el usuario)
- `postgresql-C-Q2-c1-r3` ok (01:37:08) → **`paused` 01:37:10** → reinicio de Docker (telemetría recreada ≈ 01:38, 12 min de vida) → **`resumed` 01:38:27** → `postgresql-A-Q2-c1-r3` ok → en curso `mongodb-C-Q2-c1-r3` (297 líneas de `series.csv`). Pausa de ≈ 1 min 17 s; ninguna corrida perdida. **Sin incidencias nuevas** (9 de 12). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 12.7 h.
- `mediciones.csv`: 75 filas (56 ok, 5 oom, 14 spill). Reinicios preventivos de Docker del 31-oct/1-nov: 14:24, 15:53, 17:19, 23:15, 00:30, 01:38 (+ el del atasco 18:26–21:56). Aplicar el reinicio cada ≈ 60–70 min ha mantenido la tasa de incidencias en ≈ 1 por 2–3 reinicios.

### 01:51 — 75/180 corridas — normal
- Desde el reinicio de 01:38: `postgresql-A-Q2-c1-r3` ok; en curso `mongodb-C-Q2-c1-r3`. `mediciones.csv`: 75 filas (56 ok, 14 spill, 5 oom). Restante ≈ 12.7 h. Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 13 min desde el último `resumed` (01:38:27); próximo ≈ 02:38 (revisión de las 02:44).

### 02:11 (01-nov) — 77/180 corridas — normal (1 descarte recuperado)
- Nuevas desde las 01:51: `mongodb-C-Q2-c1-r3` ok; `postgresql-A-Q1-c50-r3` **descartada 1 vez por `telemetry_gap` (≈ 02:00)** y repetida con éxito (`spill`, esperado; 02:07:03). En curso: `mongodb-A-Q1-c50-r3` (206 líneas de `series.csv`). **Sin incidencias nuevas** (9 de 12). `docker version` 0.2 s; cAdvisor healthy (32 min); watchdog sin alertas; restante ≈ 12.5 h.
- `mediciones.csv`: 77 filas (57 ok, 5 oom, 15 spill). Costo del descarte ≈ 7 min. Próximo reinicio preventivo sugerido: ≈ 02:45–03:00.

### 02:11 — 77/180 corridas — normal (1 `telemetry_gap` aislado recuperado)
- Desde las 01:51: `mongodb-C-Q2-c1-r3` ok; `postgresql-A-Q1-c50-r3` spill (esperado, tras 1 descarte por `telemetry_gap` a las 02:00:10); en curso `mongodb-A-Q1-c50-r3`. `mediciones.csv`: 77 filas (57 ok, 15 spill, 5 oom). Restante ≈ 12.5 h. Sin incidencias nuevas (9/12); `docker version` 0.2 s; telemetría healthy; watchdog activo. Sin reinicio: descarte aislado con Docker rápido; 32 min desde el último `resumed` (01:38:27); próximo ≈ 02:38 (revisión de las 02:44).

### 02:31 (01-nov) — 80/180 corridas — normal (44 % del plan)
- Nuevas desde las 02:11: `mongodb-A-Q1-c50-r3` ok, `mongodb-C-Q1-c1-r3` ok, `postgresql-A-Q1-c10-r3` spill (esperado). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-C-Q1-c1-r3` (130 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (52 min); watchdog sin alertas; restante ≈ 12.1 h.
- `mediciones.csv`: 80 filas (59 ok, 5 oom, 16 spill). Ventana sana actual: 52 min desde el reinicio de las 01:38. Reinicio preventivo sugerido ahora (02:45–03:00).

### 02:31 — 80/180 corridas — normal
- Desde las 02:11: `mongodb-A-Q1-c50-r3` ok, `mongodb-C-Q1-c1-r3` ok, `postgresql-A-Q1-c10-r3` spill (esperado); en curso `postgresql-C-Q1-c1-r3`. `mediciones.csv`: 80 filas (59 ok, 16 spill, 5 oom). Restante ≈ 12.1 h (44 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 53 min desde el último `resumed` (01:38:27) → se ejecutará en la revisión de las 02:44.

### 02:51 (01-nov) — 83/180 corridas — normal (sin descartes en 1 h 13 min)
- Nuevas desde las 02:31: `postgresql-C-Q1-c1-r3` spill (esperado), `mongodb-A-Q2-c50-r3` ok, `mongodb-B-Q2-c1-r3` ok. **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-A-Q1-c1-r3` (48 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (>1 h, sin reinicio desde las 01:38); watchdog sin alertas; restante ≈ 11.7 h.
- `mediciones.csv`: 83 filas (61 ok, 5 oom, 17 spill). La ventana sin degradación ya es de **≈ 1 h 13 min** (desde las 01:38) sin descartar ninguna corrida desde las 02:00 — la segunda más larga tras la de la tarde. Sin síntomas de degradación; reinicio preventivo opcional.

### 02:52 — 83/180 corridas — 73 min desde el último `resumed`: se inicia reinicio preventivo #8
- Desde las 02:31: `postgresql-C-Q1-c1-r3` spill (esperado), `mongodb-A-Q2-c50-r3` ok, `mongodb-B-Q2-c1-r3` ok; en curso `mongodb-A-Q1-c1-r3`. Restante ≈ 11.7 h. Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Umbral de 60 min superado (72.9 min desde `resumed` 01:38:27) → pausa pedida a las 02:52.

### 02:58 — 84/180 — Reinicio preventivo #8 completado
- `paused` a las 02:57:00 (tras `mongodb-A-Q1-c1-r3` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 15 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 02:58:01. `docker version` 0.1 s; watchdog activo; incidencias 9/12. Restante ≈ 11.6 h (fin estimado ≈ domingo 14:30). Próximo reinicio preventivo ≈ 03:58.

### 03:11 (01-nov) — 85/180 corridas — normal (reinicio preventivo de Docker por el usuario)
- `mongodb-A-Q1-c1-r3` ok (02:57:00) → **`paused` 02:57:00** → reinicio de Docker (telemetría recreada ≈ 02:58, 12 min de vida) → **`resumed` 02:58:01** → `postgresql-B-Q1-c10-r3` spill (esperado) → en curso `mongodb-C-Q1-c50-r3` (321 líneas de `series.csv`). Pausa de ≈ 1 min; ninguna corrida perdida. **Sin incidencias nuevas** (9 de 12). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 11.5 h.
- `mediciones.csv`: 85 filas (62 ok, 5 oom, 18 spill). Reinicios preventivos del 1-nov: 00:30, 01:38, 02:57 (≈ cada 70–80 min).

### 03:11 — 85/180 corridas — normal
- Desde el reinicio de 02:58: `postgresql-B-Q1-c10-r3` spill (esperado); en curso `mongodb-C-Q1-c50-r3`. `mediciones.csv`: 85 filas (62 ok, 18 spill, 5 oom). Restante ≈ 11.5 h. Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 13 min desde el último `resumed` (02:58:01); próximo ≈ 03:58 (revisión de las 04:04).

### 03:31 (01-nov) — 88/180 corridas — normal
- Nuevas desde las 03:11: `mongodb-C-Q1-c50-r3` ok, `postgresql-B-Q2-c10-r3` ok, `postgresql-C-Q2-c50-r3` ok. **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-B-Q2-c50-r3` (233 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (32 min); watchdog sin alertas; restante ≈ 11.1 h.
- `mediciones.csv`: 88 filas (65 ok, 5 oom, 18 spill). ≈ 49 % del plan de 180.

### 03:31 — 88/180 corridas — normal
- Desde las 03:11: `mongodb-C-Q1-c50-r3`, `postgresql-B-Q2-c10-r3`, `postgresql-C-Q2-c50-r3` ok; en curso `mongodb-B-Q2-c50-r3`. `mediciones.csv`: 88 filas (65 ok, 18 spill, 5 oom). Restante ≈ 11.1 h (49 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 33 min desde el último `resumed` (02:58:01); próximo ≈ 03:58 (revisión de las 04:04).

### 03:51 (01-nov) — 91/180 corridas — normal (sin descartes en 1 h 40 min)
- Nuevas desde las 03:31: `mongodb-B-Q2-c50-r3`, `postgresql-A-Q2-c10-r3`, `mongodb-A-Q1-c10-r3`, todas ok. **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-C-Q1-c10-r3` (154 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (52 min); watchdog sin alertas; restante ≈ 10.7 h.
- `mediciones.csv`: 91 filas (68 ok, 5 oom, 18 spill). Racha sin ningún descarte desde las 02:07 (≈ 1 h 44 min), la mejor desde la mañana. Reinicio preventivo opcional (≈ 04:00–04:15).

### 03:51 — 91/180 corridas — normal
- Desde las 03:31: `mongodb-B-Q2-c50-r3`, `postgresql-A-Q2-c10-r3`, `mongodb-A-Q1-c10-r3` ok; en curso `postgresql-C-Q1-c10-r3`. `mediciones.csv`: 91 filas (68 ok, 18 spill, 5 oom). Restante ≈ 10.7 h. Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 53 min desde el último `resumed` (02:58:01) → se ejecutará en la revisión de las 04:04.

### 04:11 (01-nov) — 94/180 corridas — normal (sin descartes en 2 h 04 min)
- Nuevas desde las 03:51: `postgresql-C-Q1-c10-r3` spill (esperado), `postgresql-A-Q2-c50-r3` ok, `postgresql-C-Q2-c10-r3` ok. **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-B-Q2-c50-r3` (70 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (>1 h, sin reinicio de Docker desde las 02:58); watchdog sin alertas; restante ≈ 10.4 h.
- `mediciones.csv`: 94 filas (70 ok, 5 oom, 19 spill). Racha sin descartes desde las 02:07 ≈ **2 h 04 min** — la más larga desde el reinicio del equipo; sin reinicio de Docker en 1 h 13 min. Se sigue sin síntomas de degradación.

### 04:12 — 94/180 corridas — 73 min desde el último `resumed`: se inicia reinicio preventivo #9
- Desde las 03:51: `postgresql-C-Q1-c10-r3` spill (esperado), `postgresql-A-Q2-c50-r3` ok, `postgresql-C-Q2-c10-r3` ok; en curso `postgresql-B-Q2-c50-r3`. Restante ≈ 10.4 h. Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Umbral de 60 min superado (73.4 min desde `resumed` 02:58:01) → pausa pedida a las 04:12.

### 04:18 — 95/180 — Reinicio preventivo #9 completado
- `paused` a las 04:16:39 (tras `postgresql-B-Q2-c50-r3` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 15 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 04:17:44. `docker version` 0.1 s; watchdog activo; incidencias 9/12. Restante ≈ 10.3 h (fin estimado ≈ domingo 14:30). Próximo reinicio preventivo ≈ 05:17.

### 04:31 (01-nov) — 96/180 corridas — normal (reinicio preventivo de Docker por el usuario)
- `postgresql-B-Q2-c50-r3` ok (04:16:38) → **`paused` 04:16:40** → reinicio de Docker (telemetría recreada ≈ 04:17, 13 min de vida) → **`resumed` 04:17:45** → `mongodb-C-Q2-c10-r3` ok → en curso `postgresql-B-Q1-c50-r3` (337 líneas de `series.csv`; esta celda dio `oom` en r1 y r2). Pausa de ≈ 1 min; ninguna corrida perdida. **Sin incidencias nuevas** (9 de 12). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 10.1 h.
- `mediciones.csv`: 96 filas (72 ok, 5 oom, 19 spill). Reinicios preventivos del 1-nov: 00:30, 01:38, 02:57, 04:17 (≈ cada 70–80 min). Si `postgresql-B-Q1-c50-r3` da `oom`, serán 3 de 3 repeticiones iguales.

### 04:31 — 96/180 corridas — normal
- Desde el reinicio de 04:18: `mongodb-C-Q2-c10-r3` ok; en curso `postgresql-B-Q1-c50-r3` (se espera OOM, como r1 y r2 del perfil B). `mediciones.csv`: 96 filas (72 ok, 19 spill, 5 oom). Restante ≈ 10.1 h. Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 13 min desde el último `resumed` (04:17:44); próximo ≈ 05:17 (revisión de las 05:24).

### 04:51 (01-nov) — 99/180 corridas — normal; `postgresql / PROFILE_B / Q1 / c=50` → `oom` 3 de 3
- Nuevas desde las 04:31: **`postgresql-B-Q1-c50-r3` → `oom`** (3 de 3 repeticiones iguales: r1, r2 y r3 → celda determinista), `postgresql-A-Q1-c1-r3` spill (esperado), `mongodb-B-Q1-c50-r3` ok. En curso: `mongodb-A-Q2-c1-r3` (257 líneas de `series.csv`). **Sin descartes ni incidencias nuevas** (9 de 12). `docker version` 0.1 s; cAdvisor healthy (33 min); watchdog sin alertas; restante ≈ 9.8 h.
- `mediciones.csv`: 99 filas (73 ok, 6 oom, 20 spill). Las 6 corridas `oom` corresponden a PostgreSQL con Q1 y c=50 en los perfiles B y C (3 repeticiones cada uno); en el perfil A esa misma celda termina en `spill`. ≈ 55 % del plan.

### 04:51 — 99/180 corridas — normal (6.º `oom`: PROFILE_B r3 consistente)
- Desde las 04:31: **`postgresql-B-Q1-c50-r3` → `oom`** (PROFILE_B, tercera repetición con OOM: r1, r2 y r3 → resultado M2 consistente), `postgresql-A-Q1-c1-r3` spill (esperado), `mongodb-B-Q1-c50-r3` ok; en curso `mongodb-A-Q2-c1-r3`. `mediciones.csv`: 99 filas (73 ok, 20 spill, 6 oom). Restante ≈ 9.8 h (55 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 34 min desde el último `resumed` (04:17:44); próximo ≈ 05:17 (revisión de las 05:24).

### 05:11 (01-nov) — 102/180 corridas — normal (57 % del plan)
- Nuevas desde las 04:51: `mongodb-A-Q2-c1-r3` ok, `mongodb-B-Q2-c10-r3` ok, `postgresql-B-Q1-c1-r3` spill (esperado). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-B-Q1-c10-r3` (183 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (53 min); watchdog sin alertas; restante ≈ 9.4 h.
- `mediciones.csv`: 102 filas (75 ok, 6 oom, 21 spill). Racha sin descartes desde las 02:07 ≈ 3 h. Reinicio preventivo opcional (≈ 05:30–05:45).

### 05:11 — 102/180 corridas — normal
- Desde las 04:51: `mongodb-A-Q2-c1-r3`, `mongodb-B-Q2-c10-r3` ok; `postgresql-B-Q1-c1-r3` spill (esperado); en curso `mongodb-B-Q1-c10-r3`. `mediciones.csv`: 102 filas (75 ok, 21 spill, 6 oom). Restante ≈ 9.4 h (57 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 54 min desde el último `resumed` (04:17:44) → se ejecutará en la revisión de las 05:24.

### 05:31 (01-nov) — 105/180 corridas — normal (sin descartes en 3 h 24 min)
- Nuevas desde las 05:11: `mongodb-B-Q1-c10-r3` ok, `mongodb-C-Q2-c50-r3` ok, `postgresql-B-Q2-c1-r3` ok. **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-C-Q1-c10-r3` (104 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (>1 h, sin reinicio de Docker desde las 04:17); watchdog sin alertas; restante ≈ 9.0 h.
- `mediciones.csv`: 105 filas (78 ok, 6 oom, 21 spill). La racha sin descartes desde las 02:07 ya es de ≈ **3 h 24 min** y el último reinicio de Docker fue hace 1 h 13 min: la degradación del día parece haberse reducido mucho (¿carga del host menor de madrugada?). Vigilar igualmente.

### 05:32 — 105/180 corridas — 74 min desde el último `resumed`: se inicia reinicio preventivo #10
- Desde las 05:11: `mongodb-B-Q1-c10-r3`, `mongodb-C-Q2-c50-r3`, `postgresql-B-Q2-c1-r3` ok; en curso `mongodb-C-Q1-c10-r3`. Restante ≈ 9.0 h (58 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Umbral de 60 min superado (73.6 min desde `resumed` 04:17:44) → pausa pedida a las 05:32.

### 05:38 — 106/180 — Reinicio preventivo #10 completado
- `paused` a las 05:36:03 (tras `mongodb-C-Q1-c10-r3` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 15 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 05:37:20. `docker version` 0.1 s; watchdog activo; incidencias 9/12. Restante ≈ 8.9 h (fin estimado ≈ domingo 14:30). Próximo reinicio preventivo ≈ 06:37.

### 05:51 (01-nov) — 107/180 corridas — normal (reinicio preventivo de Docker por el usuario)
- `mongodb-C-Q1-c10-r3` ok (05:36:01) → **`paused` 05:36:03** → reinicio de Docker (telemetría recreada ≈ 05:37, 13 min de vida) → **`resumed` 05:37:20** → `mongodb-B-Q1-c1-r3` ok → en curso `mongodb-A-Q2-c10-r3` (361 líneas de `series.csv`). Pausa de ≈ 1 min 17 s; ninguna corrida perdida. **Sin incidencias nuevas** (9 de 12). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 8.8 h.
- `mediciones.csv`: 107 filas (80 ok, 6 oom, 21 spill). Reinicios preventivos del 1-nov: 00:30, 01:38, 02:57, 04:17, 05:37 (≈ cada 80 min). Racha sin descartes desde las 02:07 ≈ 3 h 44 min.

### 05:51 — 107/180 corridas — normal
- Desde el reinicio de 05:38: `mongodb-B-Q1-c1-r3` ok; en curso `mongodb-A-Q2-c10-r3`. `mediciones.csv`: 107 filas (80 ok, 21 spill, 6 oom). Restante ≈ 8.8 h (59 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 14 min desde el último `resumed` (05:37:20); próximo ≈ 06:37 (revisión de las 06:44).

### 06:11 (01-nov) — 110/180 corridas — ✅ MATRIZ PRINCIPAL COMPLETA; empieza el anexo `relaxed`
- Nuevas desde las 05:51: `mongodb-A-Q2-c10-r3` ok (cerró la **matriz principal**: `mediciones.csv` = 108 filas de datos, **las 36 celdas con exactamente 3 repeticiones**; 81 ok, 6 oom, 21 spill). Después `mongodb-C-Q2-c10-r3-relaxed` ok y `mongodb-C-Q2-c50-r3-relaxed` ok → primeras filas de `results/anexo_variantes.csv`. En curso: `mongodb-C-Q1-c50-r3-relaxed` (281 líneas de `series.csv`).
- **Sin descartes ni incidencias nuevas** (9 de 12). `docker version` 0.1 s; cAdvisor healthy (33 min); watchdog sin alertas. Restantes 70 corridas ≈ 8.4 h (todas del anexo `relaxed`: 72 en total, 2 hechas; incluye la ampliación a perfiles A y B de Q2, y Q1/Q2 en C).
- Primeros datos `relaxed` (MongoDB, perfil C, `allowDiskUse=true`, rep 3): Q2 c=10 → 0.400 QPS, P50 25.4 s; Q2 c=50 → 0.167 QPS, P50 295.4 s. Se compararán con `strict` cuando haya más celdas.
- Para el reporte: la matriz principal (36 celdas × 3 reps, `strict`) ya se puede analizar con `analysis/analyze_campaign.py`; el anexo seguirá llenándose hasta ≈ 14:30 si no hay interrupciones.

### 06:11 — 110/180 corridas — ✔ HITO: matriz principal completa (108/108); empieza el anexo `relaxed`
- Desde las 05:51: `mongodb-A-Q2-c10-r3` ok, que **cierra las 108 corridas de la matriz principal** (`results/mediciones.csv`: 108 filas = 81 ok + 21 spill + 6 oom; 3 repeticiones × 36 celdas). Siguen las corridas de la variante `relaxed` (anexo, `results/anexo_variantes.csv`): `mongodb-C-Q2-c10-r3-relaxed` y `mongodb-C-Q2-c50-r3-relaxed` ok; en curso `mongodb-C-Q1-c50-r3-relaxed`. Quedan 70 corridas (≈ 8.4 h): el resto del anexo `relaxed` (12 celdas en perfil C + 36 añadidas en A y B).
- Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 34 min desde el último `resumed` (05:37:20); próximo ≈ 06:37 (revisión de las 06:44).
- Para el reporte: la parte principal ya se puede analizar (`analysis/analyze_campaign.py`); el anexo `relaxed` es el contraste memoria estricta vs. relajada.

### 06:31 (01-nov) — 113/180 corridas — normal; primer resultado clave del anexo `relaxed`
- Nuevas desde las 06:11: `mongodb-C-Q1-c50-r3-relaxed` ok, `mongodb-C-Q1-c1-r1-relaxed` ok, **`postgresql-C-Q1-c10-r1-relaxed` → `oom`**. En curso: `postgresql-C-Q2-c1-r3-relaxed` (209 líneas de `series.csv`). **Sin descartes ni incidencias nuevas** (9 de 12). `docker version` 0.1 s; cAdvisor healthy (53 min); watchdog sin alertas; restante ≈ 8.1 h.
- `anexo_variantes.csv`: 5 filas. `mediciones.csv`: 108 filas de datos (sin cambios).
- **Confirmación de la hipótesis M2 (reporte, §4.2 punto 3):** PostgreSQL, perfil C, Q1, **c=10**: con `work_mem=4MB` (`strict`) termina en `spill`; con **`work_mem=64MB` (`relaxed`) termina en `oom`** (0 consultas, QPS 0.000). Subir `work_mem` multiplica la memoria anónima de los backends concurrentes sobre el techo de 1 GiB (sin swap) → `OOMKill`, justo lo que se había planteado. Es una sola repetición (r1); faltan r2 y r3.
- MongoDB `relaxed` (`allowDiskUse=true`) en C: Q1 c=1 → 0.580 QPS; Q1 c=50 → 0.500 QPS; Q2 c=10 → 0.400 QPS; Q2 c=50 → 0.167 QPS; todas `ok`.

### 06:31 — 113/180 corridas — normal (primer `oom` en el anexo `relaxed`, legítimo)
- Desde las 06:11: `mongodb-C-Q1-c50-r3-relaxed` ok, `mongodb-C-Q1-c1-r1-relaxed` ok, **`postgresql-C-Q1-c10-r1-relaxed` → `oom`** (PROFILE_C, `work_mem=64MB`, 10 clientes: confirma la hipótesis del reporte §4.2/§6.2-3: subir `work_mem` a 64 MB con concurrencia agota el techo de 1 GiB sin swap). En curso `postgresql-C-Q2-c1-r3-relaxed`. `anexo_variantes.csv`: 5 filas (4 ok, 1 oom). Restante ≈ 8.1 h.
- Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 54 min desde el último `resumed` (05:37:20) → se ejecutará en la revisión de las 06:44.

### 06:51 (01-nov) — 116/180 corridas — normal
- Nuevas desde las 06:31: `postgresql-C-Q2-c1-r3-relaxed` ok (QPS 1.327), `postgresql-C-Q2-c50-r2-relaxed` ok (QPS 0.540), `mongodb-C-Q1-c1-r2-relaxed` ok (QPS 0.587). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-C-Q2-c50-r2-relaxed` (125 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (>1 h, sin reinicio de Docker desde las 05:37); watchdog sin alertas; restante ≈ 7.7 h.
- `anexo_variantes.csv`: 8 filas de datos; `mediciones.csv` sin cambios (108). Racha sin descartes desde las 02:07 ≈ 4 h 45 min; Docker lleva 1 h 14 min sin reinicio, sin síntomas.

### 06:52 — 116/180 corridas — 74 min desde el último `resumed`: se inicia reinicio preventivo #11
- Desde las 06:31: `postgresql-C-Q2-c1-r3-relaxed`, `postgresql-C-Q2-c50-r2-relaxed`, `mongodb-C-Q1-c1-r2-relaxed` ok; en curso `mongodb-C-Q2-c50-r2-relaxed`. Restante ≈ 7.7 h (64 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Umbral de 60 min superado (74 min desde `resumed` 05:37:20) → pausa pedida a las 06:52.

### 06:57 — 117/180 — Reinicio preventivo #11 completado
- `paused` a las 06:55:42 (tras `mongodb-C-Q2-c50-r2-relaxed` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 13 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 06:56:56. `docker version` 0.2 s; watchdog activo; incidencias 9/12. Restante ≈ 7.6 h (fin estimado ≈ domingo 14:30). Próximo reinicio preventivo ≈ 07:57.

### 07:11 (01-nov) — 118/180 corridas — normal (reinicio preventivo de Docker por el usuario)
- `mongodb-C-Q2-c50-r2-relaxed` ok (06:55:41) → **`paused` 06:55:42** → reinicio de Docker (telemetría recreada ≈ 06:57, 13 min de vida) → **`resumed` 06:56:56** → `mongodb-C-Q2-c1-r2-relaxed` ok → en curso `postgresql-C-Q2-c10-r1-relaxed` (60 líneas de `series.csv`). Pausa de ≈ 1 min 15 s; ninguna corrida perdida. **Sin incidencias nuevas** (9 de 12). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 7.5 h.
- `anexo_variantes.csv`: 10 filas de datos (de 72 previstas); `mediciones.csv` sin cambios (108). Reinicios preventivos del 1-nov: 00:30, 01:38, 02:57, 04:17, 05:37, 06:56 (≈ cada 80 min).

### 07:11 — 118/180 corridas — normal (1 `telemetry_gap` aislado; sin reinicio)
- Desde el reinicio de 06:57: `mongodb-C-Q2-c1-r2-relaxed` ok; `postgresql-C-Q2-c10-r1-relaxed`: 1.er intento **descartado por `telemetry_gap`** (07:11:08), reintento en curso. `anexo_variantes.csv`: 10 filas (9 ok, 1 oom). Restante ≈ 7.5 h. Sin incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Sin reinicio: descarte aislado con Docker rápido; 14 min desde el último `resumed` (06:56:56); próximo ≈ 07:57 (revisión de las 08:04).

### 07:31 (01-nov) — 120/180 corridas — normal (1 descarte recuperado)
- Nuevas desde las 07:11: `postgresql-C-Q2-c10-r1-relaxed` **descartada 1 vez por `telemetry_gap` (≈ 07:11)**, repetida con éxito (`ok`, QPS 0.763; 07:18:14); `mongodb-C-Q2-c50-r1-relaxed` ok (07:25:24). En curso: `mongodb-C-Q1-c50-r2-relaxed` (306 líneas de `series.csv`). **Sin incidencias nuevas** (9 de 12). `docker version` 0.1 s; cAdvisor healthy (33 min); watchdog sin alertas; restante ≈ 7.2 h.
- `anexo_variantes.csv`: 12 filas de datos de 72 previstas (≈ 17 %); `mediciones.csv` sin cambios (108). Costo del descarte ≈ 7 min.

### 07:31 — 120/180 corridas — normal
- Desde las 07:11: `postgresql-C-Q2-c10-r1-relaxed` ok (tras el reintento), `mongodb-C-Q2-c50-r1-relaxed` ok; en curso `mongodb-C-Q1-c50-r2-relaxed`. `anexo_variantes.csv`: 12 filas (11 ok, 1 oom). Restante ≈ 7.2 h (67 % completado). Sin incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 34 min desde el último `resumed` (06:56:56); próximo ≈ 07:57 (revisión de las 08:04).

### 07:51 (01-nov) — 123/180 corridas — normal
- Nuevas desde las 07:31: `mongodb-C-Q1-c50-r2-relaxed` ok (0.500 QPS), `mongodb-C-Q1-c10-r1-relaxed` ok (0.480 QPS), `postgresql-C-Q1-c1-r1-relaxed` **spill** (0.177 QPS; con `work_mem=64MB` y c=1 sigue volcando a disco pero rinde algo más que `strict`, ≈ 0.145). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-C-Q2-c1-r3-relaxed` (230 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (53 min); watchdog sin alertas; restante ≈ 6.9 h.
- `anexo_variantes.csv`: 15 filas de datos de 72 previstas (≈ 21 %); `mediciones.csv` sin cambios (108). Observación para el reporte (M2): con c=1 `relaxed` mejora PostgreSQL ≈ +20 % en QPS en el perfil C (0.177 vs ≈ 0.145), mientras que con c=10 produce `oom` (06:31) → efecto opuesto según concurrencia, tal como plantea el reporte §4.2.

### 07:51 — 123/180 corridas — normal
- Desde las 07:31: `mongodb-C-Q1-c50-r2-relaxed` ok, `mongodb-C-Q1-c10-r1-relaxed` ok, `postgresql-C-Q1-c1-r1-relaxed` spill (esperado); en curso `mongodb-C-Q2-c1-r3-relaxed`. Restante ≈ 6.9 h (68 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 54 min desde el último `resumed` (06:56:56) → se ejecutará en la revisión de las 08:04.

### 08:11 (01-nov) — 126/180 corridas — normal
- Nuevas desde las 07:51: `mongodb-C-Q2-c1-r3-relaxed` ok (0.193 QPS); **`postgresql-C-Q1-c50-r3-relaxed` y `postgresql-C-Q1-c50-r2-relaxed` → `oom`** (el `relaxed` en C/Q1/c=50 ya da `oom` en las repeticiones 2 y 3, igual que `strict`). En curso: `mongodb-C-Q2-c10-r2-relaxed` (93 líneas de `series.csv`). **Sin descartes ni incidencias nuevas** (9 de 12). `docker version` 0.1 s; cAdvisor healthy (>1 h, sin reinicio de Docker desde las 06:56); watchdog sin alertas; restante ≈ 6.5 h.
- `anexo_variantes.csv`: 18 filas de datos de 72 previstas (25 %); `mediciones.csv` sin cambios (108).

### 08:12 — 126/180 corridas — 74 min desde el último `resumed`: se inicia reinicio preventivo #12
- Desde las 07:51: `mongodb-C-Q2-c1-r3-relaxed` ok; **`postgresql-C-Q1-c50-r3-relaxed` y `postgresql-C-Q1-c50-r2-relaxed` → `oom`** (PROFILE_C con `work_mem=64MB` y 50 clientes: OOM reproducido en r1–r3 del anexo, resultado M2 legítimo); en curso `mongodb-C-Q2-c10-r2-relaxed`. Restante ≈ 6.5 h (70 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Umbral de 60 min superado (74.4 min desde `resumed` 06:56:56) → pausa pedida a las 08:12.

### 08:18 — 127/180 — Reinicio preventivo #12 completado
- `paused` a las 08:16:19 (tras `mongodb-C-Q2-c10-r2-relaxed` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 13 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 08:17:22. `docker version` 0.2 s; watchdog activo; incidencias 9/12. Restante ≈ 6.4 h (fin estimado ≈ domingo 14:40). Próximo reinicio preventivo ≈ 09:17.

### 08:31 (01-nov) — 128/180 corridas — normal (reinicio preventivo de Docker por el usuario)
- `mongodb-C-Q2-c10-r2-relaxed` ok (08:16:17) → **`paused` 08:16:19** → reinicio de Docker (telemetría recreada ≈ 08:17, 13 min de vida) → **`resumed` 08:17:22** → `postgresql-C-Q1-c50-r1-relaxed` → `oom` (completa las 3 repeticiones `oom` de C/Q1/c=50 `relaxed`: r1, r2, r3) → en curso `mongodb-C-Q2-c1-r1-relaxed` (358 líneas de `series.csv`). Pausa de ≈ 1 min 3 s; ninguna corrida perdida. **Sin incidencias nuevas** (9 de 12). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 6.3 h.
- `anexo_variantes.csv`: 20 filas de datos de 72 previstas (28 %); `mediciones.csv` sin cambios (108). Reinicios preventivos del 1-nov: 00:30, 01:38, 02:57, 04:17, 05:37, 06:56, 08:16 (≈ cada 80 min).

### 08:31 — 128/180 corridas — normal
- Desde el reinicio de 08:17: `postgresql-C-Q1-c50-r1-relaxed` → `oom` (4.º OOM del anexo, legítimo); en curso `mongodb-C-Q2-c1-r1-relaxed`. `anexo_variantes.csv`: 20 filas (15 ok, 4 oom, 1 spill). Restante ≈ 6.3 h (71 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; telemetría healthy; watchdog activo. Reinicio preventivo: 14 min desde el último `resumed` (08:17:22); próximo ≈ 09:17 (revisión de las 09:24).

### 08:51 (01-nov) — 131/180 corridas — normal
- Nuevas desde las 08:31: `mongodb-C-Q2-c1-r1-relaxed` ok (0.197 QPS), `postgresql-C-Q2-c10-r3-relaxed` ok (0.790 QPS), `postgresql-C-Q2-c1-r2-relaxed` ok (1.120 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-C-Q1-c1-r3-relaxed` (281 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (33 min); watchdog sin alertas; restante ≈ 5.9 h.
- `anexo_variantes.csv`: 23 filas de datos de 72 previstas (32 %); `mediciones.csv` sin cambios (108).

### 08:51 — 131/180 corridas — normal
- Desde las 08:31: `mongodb-C-Q2-c1-r1-relaxed`, `postgresql-C-Q2-c10-r3-relaxed`, `postgresql-C-Q2-c1-r2-relaxed` ok; en curso `mongodb-C-Q1-c1-r3-relaxed`. Restante ≈ 5.9 h (73 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 34 min desde el último `resumed` (08:17:22); próximo ≈ 09:17 (revisión de las 09:24).

### 09:11 (01-nov) — 134/180 corridas — normal
- Nuevas desde las 08:51: `mongodb-C-Q1-c1-r3-relaxed` ok (0.493 QPS), `postgresql-C-Q2-c10-r2-relaxed` ok (0.753 QPS), `postgresql-C-Q2-c50-r1-relaxed` ok (0.537 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-C-Q1-c1-r2-relaxed` (204 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (53 min); watchdog sin alertas; restante ≈ 5.5 h.
- `anexo_variantes.csv`: 26 filas de datos de 72 previstas (36 %); `mediciones.csv` sin cambios (108). Racha sin descartes desde las 07:11 ≈ 2 h.

### 09:11 — 134/180 corridas — normal
- Desde las 08:51: `mongodb-C-Q1-c1-r3-relaxed`, `postgresql-C-Q2-c10-r2-relaxed`, `postgresql-C-Q2-c50-r1-relaxed` ok; en curso `postgresql-C-Q1-c1-r2-relaxed`. Restante ≈ 5.5 h (74 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 54 min desde el último `resumed` (08:17:22) → se ejecutará en la revisión de las 09:31.

### 09:31 (01-nov) — 137/180 corridas — normal
- Nuevas desde las 09:11: `postgresql-C-Q1-c1-r2-relaxed` spill (0.153 QPS), `mongodb-C-Q2-c10-r1-relaxed` ok (0.290 QPS), `mongodb-C-Q1-c50-r1-relaxed` ok (0.500 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-C-Q1-c10-r2-relaxed` (118 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (>1 h, sin reinicio de Docker desde las 08:16); watchdog sin alertas; restante ≈ 5.2 h.
- `anexo_variantes.csv`: 29 filas de datos de 72 previstas (40 %); `mediciones.csv` sin cambios (108). Racha sin descartes desde las 07:11 ≈ 2 h 20 min; Docker lleva 1 h 14 min sin reinicio.

### 09:32 — 137/180 corridas — 74 min desde el último `resumed`: se inicia reinicio preventivo #13
- Desde las 09:11: `postgresql-C-Q1-c1-r2-relaxed` spill (esperado), `mongodb-C-Q2-c10-r1-relaxed` ok, `mongodb-C-Q1-c50-r1-relaxed` ok; en curso `postgresql-C-Q1-c10-r2-relaxed`. Restante ≈ 5.2 h (76 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Umbral de 60 min superado (74 min desde `resumed` 08:17:22) → pausa pedida a las 09:32.

### 09:38 — 138/180 — Reinicio preventivo #13 completado
- `paused` a las 09:35:50 (tras `postgresql-C-Q1-c10-r2-relaxed` → oom, legítimo; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 13 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 09:37:06. `docker version` 0.1 s; watchdog activo; incidencias 9/12. Restante ≈ 5.0 h (fin estimado ≈ domingo 14:40). Próximo reinicio preventivo ≈ 10:37.

### 09:51 (01-nov) — 139/180 corridas — normal (reinicio preventivo de Docker por el usuario)
- `postgresql-C-Q1-c10-r2-relaxed` → **`oom`** (09:35:48; 2.ª repetición consistente con r1: `relaxed` C/Q1/c=10 en PostgreSQL da `oom`, frente a `spill` en `strict`) → **`paused` 09:35:50** → reinicio de Docker (telemetría recreada ≈ 09:37, 13 min de vida) → **`resumed` 09:37:07** → `mongodb-C-Q1-c10-r2-relaxed` ok (0.497 QPS) → en curso `postgresql-C-Q1-c1-r3-relaxed` (386 líneas de `series.csv`). Pausa de ≈ 1 min 16 s; ninguna corrida perdida. **Sin incidencias nuevas** (9 de 12). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 4.9 h.
- `anexo_variantes.csv`: 31 filas de datos de 72 previstas (43 %); `mediciones.csv` sin cambios (108). Reinicios preventivos del 1-nov: 00:30, 01:38, 02:57, 04:17, 05:37, 06:56, 08:16, 09:35 (≈ cada 80 min).

### 09:51 — 140/180 corridas — normal
- Desde el reinicio de 09:37: `mongodb-C-Q1-c10-r2-relaxed` ok, `postgresql-C-Q1-c1-r3-relaxed` spill (esperado); en curso `postgresql-C-Q2-c50-r3-relaxed`. Restante ≈ 4.8 h (78 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 14 min desde el último `resumed` (09:37:06); próximo ≈ 10:37 (revisión de las 10:51).

### 10:11 (01-nov) — 142/180 corridas — normal
- Nuevas desde las 09:51: `postgresql-C-Q1-c1-r3-relaxed` spill (0.177 QPS), `postgresql-C-Q2-c50-r3-relaxed` ok (0.543 QPS), `mongodb-C-Q1-c10-r3-relaxed` ok (0.487 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-C-Q2-c1-r1-relaxed` (300 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (33 min); watchdog sin alertas; restante ≈ 4.6 h.
- `anexo_variantes.csv`: 34 filas de datos de 72 previstas (47 %); `mediciones.csv` sin cambios (108). Racha sin descartes desde las 07:11 ≈ 3 h.

### 10:11 — 142/180 corridas — normal
- Desde las 09:51: `postgresql-C-Q2-c50-r3-relaxed` ok, `mongodb-C-Q1-c10-r3-relaxed` ok; en curso `postgresql-C-Q2-c1-r1-relaxed`. Restante ≈ 4.6 h (79 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 34 min desde el último `resumed` (09:37:06); próximo ≈ 10:37 (revisión de las 10:31 aún no; se ejecutará en la de las 10:51).

### 10:31 (01-nov) — 145/180 corridas — normal; empieza la ampliación a los perfiles B y A
- Nuevas desde las 10:11: `postgresql-C-Q2-c1-r1-relaxed` ok (1.297 QPS); **`postgresql-C-Q1-c10-r3-relaxed` → `oom`** (3 de 3: r1, r2, r3 → `relaxed` en C/Q1/c=10 es `oom` determinista en PostgreSQL); `mongodb-B-Q2-c10-r2-relaxed` ok (1.057 QPS; primera corrida del perfil B del anexo ampliado). En curso: `mongodb-B-Q2-c50-r2-relaxed` (221 líneas de `series.csv`). **Sin descartes ni incidencias nuevas** (9 de 12). `docker version` 0.1 s; cAdvisor healthy (53 min); watchdog sin alertas; restante ≈ 4.2 h.
- `anexo_variantes.csv`: 37 filas de datos de 72 previstas (51 %); `mediciones.csv` sin cambios (108). Ya terminó la parte de C (Q1 y Q2, 3 concurrencias × 2 motores × 3 reps = 36 filas) y arranca la ampliación a Q2 en perfiles A y B (36 corridas).

### 10:31 — 145/180 corridas — normal
- Desde las 10:11: `postgresql-C-Q2-c1-r1-relaxed` ok, `postgresql-C-Q1-c10-r3-relaxed` → `oom` (legítimo; r1, r2 y r3 del anexo con OOM en PROFILE_C c=10), `mongodb-B-Q2-c10-r2-relaxed` ok; en curso `mongodb-B-Q2-c50-r2-relaxed`. Restante ≈ 4.2 h (81 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 54 min desde el último `resumed` (09:37:06) → se ejecutará en la revisión de las 10:51.

### 10:51 (01-nov) — 148/180 corridas — normal
- Nuevas desde las 10:31: `mongodb-B-Q2-c50-r2-relaxed` ok (0.633 QPS), `mongodb-A-Q2-c1-r1-relaxed` ok (1.927 QPS; primera del perfil A del anexo), `postgresql-B-Q2-c10-r2-relaxed` ok (2.397 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-B-Q2-c50-r1-relaxed` (142 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (>1 h, sin reinicio de Docker desde las 09:35); watchdog sin alertas; restante ≈ 3.8 h.
- `anexo_variantes.csv`: 40 filas de datos de 72 previstas (56 %); `mediciones.csv` sin cambios (108). Racha sin descartes desde las 07:11 ≈ 3 h 40 min; Docker lleva 1 h 15 min sin reinicio, sin síntomas.

### 10:52 — 148/180 corridas — 74 min desde el último `resumed`: se inicia reinicio preventivo #14
- Desde las 10:31: `mongodb-B-Q2-c50-r2-relaxed`, `mongodb-A-Q2-c1-r1-relaxed`, `postgresql-B-Q2-c10-r2-relaxed` ok; en curso `postgresql-B-Q2-c50-r1-relaxed`. Restante ≈ 3.8 h (82 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Umbral de 60 min superado (74 min desde `resumed` 09:37:06) → pausa pedida a las 10:52.

### 10:57 — 149/180 — Reinicio preventivo #14 completado
- `paused` a las 10:55:25 (tras `postgresql-B-Q2-c50-r1-relaxed` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 13 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 10:56:37. `docker version` 0.1 s; watchdog activo; incidencias 9/12. Restante ≈ 3.7 h (fin estimado ≈ domingo 14:40). Próximo reinicio preventivo ≈ 11:56.

### 11:11 (01-nov) — 150/180 corridas — normal (reinicio preventivo de Docker por el usuario)
- `postgresql-B-Q2-c50-r1-relaxed` ok (10:55:23; 3.023 QPS) → **`paused` 10:55:25** → reinicio de Docker (telemetría recreada ≈ 10:56, 14 min de vida) → **`resumed` 10:56:37** → `postgresql-B-Q2-c1-r3-relaxed` ok (2.630 QPS) → en curso `mongodb-A-Q2-c50-r1-relaxed` (398 líneas de `series.csv`). Pausa de ≈ 1 min 12 s; ninguna corrida perdida. **Sin incidencias nuevas** (9 de 12). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 3.6 h.
- `anexo_variantes.csv`: 42 filas de datos de 72 previstas (58 %); `mediciones.csv` sin cambios (108). Reinicios preventivos del 1-nov: 00:30, 01:38, 02:57, 04:17, 05:37, 06:56, 08:16, 09:35, 10:55 (≈ cada 80 min).

### 11:11 — 151/180 corridas — normal
- Desde el reinicio de 10:57: `postgresql-B-Q2-c1-r3-relaxed` ok, `mongodb-A-Q2-c50-r1-relaxed` ok; en curso `mongodb-B-Q2-c50-r1-relaxed`. Restante ≈ 3.5 h (84 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 14 min desde el último `resumed` (10:56:37); próximo ≈ 11:56 (revisión de las 12:11 si no hay síntomas antes).

### 11:31 (01-nov) — 153/180 corridas — normal (85 % del plan)
- Nuevas desde las 11:11: `mongodb-A-Q2-c50-r1-relaxed` ok (4.660 QPS), `mongodb-B-Q2-c50-r1-relaxed` ok (0.643 QPS), `postgresql-A-Q2-c10-r2-relaxed` ok (4.860 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-A-Q2-c10-r1-relaxed` (316 líneas de `series.csv`). `docker version` 0.2 s; cAdvisor healthy (34 min); watchdog sin alertas; restante ≈ 3.2 h.
- `anexo_variantes.csv`: 45 filas de datos de 72 previstas (63 %); `mediciones.csv` sin cambios (108). Racha sin descartes desde las 07:11 ≈ 4 h 20 min.

### 11:31 — 153/180 corridas — normal
- Desde las 11:11: `mongodb-B-Q2-c50-r1-relaxed`, `postgresql-A-Q2-c10-r2-relaxed` ok; en curso `postgresql-A-Q2-c10-r1-relaxed`. Restante ≈ 3.2 h (85 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 35 min desde el último `resumed` (10:56:37); próximo ≈ 11:56 (se ejecutará en la revisión de las 11:51 si ya hay ≥ 60 min, si no en la de las 12:11).

### 11:51 (01-nov) — 156/180 corridas — normal
- Nuevas desde las 11:31: `postgresql-A-Q2-c10-r1-relaxed` ok (4.937 QPS), `postgresql-A-Q2-c50-r2-relaxed` ok (5.443 QPS), `postgresql-B-Q2-c1-r1-relaxed` ok (2.377 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-B-Q2-c1-r2-relaxed` (236 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (54 min); watchdog sin alertas; restante ≈ 2.9 h.
- `anexo_variantes.csv`: 48 filas de datos de 72 previstas (67 %); `mediciones.csv` sin cambios (108). Racha sin descartes desde las 07:11 ≈ 4 h 40 min.

### 11:51 — 156/180 corridas — normal
- Desde las 11:31: `postgresql-A-Q2-c10-r1-relaxed`, `postgresql-A-Q2-c50-r2-relaxed`, `postgresql-B-Q2-c1-r1-relaxed` ok; en curso `mongodb-B-Q2-c1-r2-relaxed`. Restante ≈ 2.9 h (87 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 55 min desde el último `resumed` (10:56:37) → se ejecutará en la revisión de las 12:11.

### 12:11 (01-nov) — 159/180 corridas — normal
- Nuevas desde las 11:51: `mongodb-B-Q2-c1-r2-relaxed` ok (0.383 QPS), `mongodb-B-Q2-c10-r3-relaxed` ok (0.793 QPS), `postgresql-A-Q2-c10-r3-relaxed` ok (4.987 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-B-Q2-c50-r3-relaxed` (155 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (>1 h, sin reinicio de Docker desde las 10:55); watchdog sin alertas; restante ≈ 2.5 h.
- `anexo_variantes.csv`: 51 filas de datos de 72 previstas (71 %); `mediciones.csv` sin cambios (108). Racha sin descartes desde las 07:11 ≈ 5 h; Docker lleva 1 h 15 min sin reinicio, sin síntomas.

### 12:12 — 159/180 corridas — 75 min desde el último `resumed`: se inicia reinicio preventivo #15
- Desde las 11:51: `mongodb-B-Q2-c1-r2-relaxed`, `mongodb-B-Q2-c10-r3-relaxed`, `postgresql-A-Q2-c10-r3-relaxed` ok; en curso `postgresql-B-Q2-c50-r3-relaxed`. Restante ≈ 2.5 h (88 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Umbral de 60 min superado (74.7 min desde `resumed` 10:56:37) → pausa pedida a las 12:12.

### 12:17 — 160/180 — Reinicio preventivo #15 completado
- `paused` a las 12:15:12 (tras `postgresql-B-Q2-c50-r3-relaxed` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 13 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 12:16:22. `docker version` 0.2 s; watchdog activo; incidencias 9/12. Quedan 20 corridas ≈ 2.4 h (fin estimado ≈ domingo 14:40). Próximo reinicio preventivo ≈ 13:16.

### 12:31 (01-nov) — 162/180 corridas — normal (reinicio preventivo de Docker por el usuario)
- `postgresql-B-Q2-c50-r3-relaxed` ok (12:15:10; 2.830 QPS) → **`paused` 12:15:12** → reinicio de Docker (telemetría recreada ≈ 12:16, 14 min de vida) → **`resumed` 12:16:22** → `postgresql-B-Q2-c10-r1-relaxed` ok (2.327 QPS) → `postgresql-A-Q2-c1-r2-relaxed` ok (3.973 QPS) → en curso `mongodb-A-Q2-c10-r1-relaxed` (recién iniciada). Pausa de ≈ 1 min 10 s; ninguna corrida perdida. **Sin incidencias nuevas** (9 de 12). `docker version` 0.2 s; watchdog sin alertas; restante ≈ 2.2 h.
- `anexo_variantes.csv`: 54 filas de datos de 72 previstas (75 %); `mediciones.csv` sin cambios (108). Reinicios preventivos del 1-nov: 00:30, 01:38, 02:57, 04:17, 05:37, 06:56, 08:16, 09:35, 10:55, 12:15 (≈ cada 80 min).

### 12:31 — 162/180 corridas — normal
- Desde el reinicio de 12:17: `postgresql-B-Q2-c10-r1-relaxed` ok, `postgresql-A-Q2-c1-r2-relaxed` ok; en curso `mongodb-A-Q2-c10-r1-relaxed`. Restante ≈ 2.2 h (90 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 15 min desde el último `resumed` (12:16:22); próximo ≈ 13:16 (se evaluará en la revisión de las 13:31).

### 12:51 (01-nov) — 164/180 corridas — normal
- Nuevas desde las 12:31: `mongodb-A-Q2-c10-r1-relaxed` ok (4.803 QPS), `mongodb-A-Q2-c50-r2-relaxed` ok (4.530 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-B-Q2-c1-r1-relaxed` (334 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (34 min); watchdog sin alertas; restante ≈ 1.9 h.
- `anexo_variantes.csv`: 56 filas de datos de 72 previstas (78 %); `mediciones.csv` sin cambios (108). Quedan 16 corridas.

### 12:51 — 164/180 corridas — normal
- Desde las 12:31: `mongodb-A-Q2-c10-r1-relaxed`, `mongodb-A-Q2-c50-r2-relaxed` ok; en curso `mongodb-B-Q2-c1-r1-relaxed`. Restante ≈ 1.9 h (91 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 35 min desde el último `resumed` (12:16:22); próximo ≈ 13:16 (se evaluará en la revisión de las 13:11 si ya cumple 60 min, si no en la de las 13:31).

### 13:11 (01-nov) — 167/180 corridas — normal
- Nuevas desde las 12:51: `mongodb-B-Q2-c1-r1-relaxed` ok (0.383 QPS), `mongodb-B-Q2-c1-r3-relaxed` ok (0.383 QPS), `mongodb-B-Q2-c50-r3-relaxed` ok (0.653 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-A-Q2-c50-r1-relaxed` (247 líneas de `series.csv`). `docker version` 0.2 s; cAdvisor healthy (54 min); watchdog sin alertas; restante ≈ 1.6 h.
- `anexo_variantes.csv`: 59 filas de datos de 72 previstas (82 %); `mediciones.csv` sin cambios (108). Quedan 13 corridas (≈ 1 h 35 min). Racha sin descartes desde las 07:11 ≈ 6 h.

### 13:11 — 167/180 corridas — normal
- Desde las 12:51: `mongodb-B-Q2-c1-r1-relaxed`, `mongodb-B-Q2-c1-r3-relaxed`, `mongodb-B-Q2-c50-r3-relaxed` ok; en curso `postgresql-A-Q2-c50-r1-relaxed`. Restante ≈ 1.6 h (93 % completado; 13 corridas). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.2 s; watchdog activo. Reinicio preventivo: 55 min desde el último `resumed` (12:16:22) → se ejecutará en la revisión de las 13:31.

### 13:31 (01-nov) — 170/180 corridas — normal; faltan 10
- Nuevas desde las 13:11: `postgresql-A-Q2-c50-r1-relaxed` ok (5.303 QPS), `postgresql-A-Q2-c50-r3-relaxed` ok (5.423 QPS), `mongodb-B-Q2-c10-r1-relaxed` ok (0.760 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-A-Q2-c1-r2-relaxed` (166 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (>1 h, sin reinicio de Docker desde las 12:15); watchdog sin alertas; restante ≈ 1.2 h (fin estimado ≈ 14:45).
- `anexo_variantes.csv`: 62 filas de datos de 72 previstas (86 %); `mediciones.csv` sin cambios (108). Racha sin descartes desde las 07:11 ≈ 6 h 20 min. Al terminar, el orquestador debería registrar `done` en el journal y el watchdog saldrá solo ("Campaña terminada").

### 13:32 — 170/180 corridas — 75 min desde el último `resumed`: se inicia reinicio preventivo #16
- Desde las 13:11: `postgresql-A-Q2-c50-r1-relaxed`, `postgresql-A-Q2-c50-r3-relaxed`, `mongodb-B-Q2-c10-r1-relaxed` ok; en curso `mongodb-A-Q2-c1-r2-relaxed`. Restante ≈ 1.2 h (94 % completado; 10 corridas). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Umbral de 60 min superado (75 min desde `resumed` 12:16:22) → pausa pedida a las 13:32.

### 13:37 — 171/180 — Reinicio preventivo #16 completado
- `paused` a las 13:35:01 (tras `mongodb-A-Q2-c1-r2-relaxed` ok; ninguna corrida perdida) → Docker Desktop cerrado + `wsl --shutdown` + relanzado (motor listo en ≈ 15 s) → `doctor` ✓ (Docker, dataset, manifiesto) → `resume` a las 13:36:19. `docker version` 0.2 s; watchdog activo; incidencias 9/12. Quedan 9 corridas ≈ 1.1 h (fin estimado ≈ 14:45). Es probable que sea el último reinicio preventivo necesario: la campaña terminaría antes del siguiente umbral de 60 min (14:36), aunque se evaluará en las revisiones siguientes.

### 13:51 (01-nov) — 173/180 corridas — normal (reinicio preventivo de Docker por el usuario); faltan 7
- `mongodb-A-Q2-c1-r2-relaxed` ok (13:34:58) → **`paused` 13:35:00** → reinicio de Docker (telemetría recreada ≈ 13:36, 14 min de vida) → **`resumed` 13:36:19** → `mongodb-A-Q2-c1-r3-relaxed` ok (1.877 QPS) → `mongodb-A-Q2-c10-r3-relaxed` ok (4.650 QPS) → en curso `postgresql-B-Q2-c10-r3-relaxed` (recién iniciada). Pausa de ≈ 1 min 19 s; ninguna corrida perdida. **Sin incidencias nuevas** (9 de 12). `docker version` 0.1 s; watchdog sin alertas; restante ≈ 0.8 h (fin estimado ≈ 14:40).
- `anexo_variantes.csv`: 65 filas de datos de 72 previstas (90 %); `mediciones.csv` sin cambios (108). Reinicios preventivos del 1-nov: 00:30, 01:38, 02:57, 04:17, 05:37, 06:56, 08:16, 09:35, 10:55, 12:15, 13:35.

### 13:51 — 173/180 corridas — normal
- Desde el reinicio de 13:37: `mongodb-A-Q2-c1-r3-relaxed` ok, `mongodb-A-Q2-c10-r3-relaxed` ok; en curso `postgresql-B-Q2-c10-r3-relaxed`. Quedan 7 corridas ≈ 0.8 h (96 % completado). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 15 min desde el último `resumed` (13:36:19); la campaña debería terminar (≈ 14:40) antes del siguiente umbral (14:36).

### 14:11 (01-nov) — 175/180 corridas — normal; faltan 5
- Nuevas desde las 13:51: `postgresql-B-Q2-c10-r3-relaxed` ok (2.230 QPS), `mongodb-A-Q2-c10-r2-relaxed` ok (4.653 QPS). **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `postgresql-A-Q2-c1-r3-relaxed` (343 líneas de `series.csv`). `docker version` 0.1 s; cAdvisor healthy (34 min); watchdog sin alertas; restante ≈ 0.6 h (fin estimado ≈ 14:45).
- `anexo_variantes.csv`: 67 filas de datos de 72 previstas (93 %); `mediciones.csv` sin cambios (108). Racha sin descartes desde las 07:11 ≈ 7 h.

### 14:11 — 175/180 corridas — normal
- Desde las 13:51: `postgresql-B-Q2-c10-r3-relaxed` ok, `mongodb-A-Q2-c10-r2-relaxed` ok; en curso `postgresql-A-Q2-c1-r3-relaxed`. Quedan 5 corridas ≈ 0.6 h (97 % completado; fin estimado ≈ 14:45). Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo. Reinicio preventivo: 35 min desde el último `resumed` (13:36:19); no se espera necesitar otro.

### 14:31 (01-nov) — 178/180 corridas — normal; faltan 2
- Nuevas desde las 14:11: `postgresql-A-Q2-c1-r3-relaxed` ok, `postgresql-B-Q2-c50-r2-relaxed` ok, `postgresql-B-Q2-c1-r2-relaxed` ok. **Sin descartes ni incidencias nuevas** (9 de 12). En curso: `mongodb-A-Q2-c50-r3-relaxed` (iniciada 14:26:11; es la penúltima). `docker version` 0.1 s; cAdvisor healthy (54 min); watchdog sin alertas; restante ≈ 0.2 h (fin estimado ≈ 14:45).
- `anexo_variantes.csv`: 70 filas de datos de 72 previstas (97 %); `mediciones.csv` sin cambios (108). Racha sin descartes desde las 07:11 ≈ 7 h 20 min.

### 14:31 — 178/180 corridas — normal; quedan 2
- Desde las 14:11: `postgresql-B-Q2-c50-r2-relaxed` ok, `postgresql-B-Q2-c1-r2-relaxed` ok; en curso `mongodb-A-Q2-c50-r3-relaxed` (+ 1 más después). `mediciones.csv`: 108 filas (matriz principal completa); `anexo_variantes.csv`: 70 filas de 72 previstas. Sin descartes ni incidencias nuevas (9/12); `docker version` 0.1 s; watchdog activo.
- Reinicio preventivo: 55 min desde el último `resumed` (13:36:19), pero **no se ejecutará**: faltan ≈ 15 min para terminar la campaña (fin estimado ≈ 14:45), un reinicio (≈ 4–5 min) costaría más de lo que evita y la degradación observada aparece normalmente a ≥ 60–90 min. Si antes aparece un síntoma grave (`runner_exception`, `compose_failed`, `docker version` > 2 s) se reevaluará.

---

## 7. CIERRE — Campaña terminada (01-nov-2026, 14:46:15 hora local)

**Estado final:** `done`, **180/180 corridas**. El watchdog registró «Campaña terminada» y salió solo. Las revisiones periódicas se cancelaron a las 14:51.

### 7.1 Auditoría de datos (14:51, solo lectura)
| Verificación | Resultado |
| :--- | :--- |
| `results/mediciones.csv` (matriz principal, `strict`) | **108 filas = 36 celdas × 3 repeticiones**; 0 celdas con ≠ 3 repeticiones; 0 duplicados. Estados: **81 ok, 21 spill, 6 oom** |
| `results/anexo_variantes.csv` (`relaxed`) | **72 filas = 24 celdas × 3 repeticiones** (Q1/Q2 en C + Q2 en A y B); 0 celdas incompletas; 0 duplicados. Estados: **63 ok, 3 spill, 6 oom** |
| Manifiesto SHA-256 | **Intacto** (`manifest.changed() = []`): consultas, perfiles, `docker-compose.yml`, `telemetry/`, generador y `benchmark_runner/*` sin modificar |
| Journal | 211 `run_started`, **180 `run_finished`**, 31 `run_discarded` (todos repetidos), 9 incidencias, 18 `paused`/21 `resumed`, 3 `halted`, 1 `done` |

### 7.2 Interrupciones e incidencias (para la sección de limitaciones del reporte)
- **Descartes (31 intentos, no registrados en los CSV):** `telemetry_gap` 24, `runner_exception` (`BrokenBarrierError`) 3, `compose_failed` 2, `engine_start_failed` 1, `drop_caches_failed` 1. Todas las corridas descartadas se repitieron desde cero.
- **Halts (3):** `incident_unresolved` (02:38, caída de Docker Desktop), `incident_budget_exhausted` ×2 (04:35 y 11:04; presupuesto `MAX_INCIDENTS` = 4).
- **Causa de fondo:** degradación progresiva de la E/S de Docker Desktop/WSL2 tras ≈ 1–1.5 h de uso continuo (síntomas: `docker version` de 0.1 s → 10–40 s, `fsHandler` de cAdvisor de 15–20 s, muestreo de cgroup al 22 %, `BrokenBarrierError`). Un atasco prolongado del motor (18:22–21:56 del 31-oct, ≈ 3 h 34 min) costó ≈ 30 corridas de capacidad. Los datos válidos no se vieron afectados.
- **Mitigación aplicada:** reinicios de Docker Desktop **entre corridas** con `pause` → reinicio → `resume` (≈ 1 min cada uno; la corrida en curso siempre terminó antes de pausar): 14:24, 15:53, 17:19, 23:15 (31-oct) y 00:30, 01:38, 02:57, 04:17, 05:37, 06:56, 08:16, 09:35, 10:55, 12:15, 13:35 (1-nov), además de los atascos 18:26–21:56. Con este régimen la tasa de descartes bajó mucho: entre las 02:07 y las 14:46 del 1-nov solo hubo 2 (02:00 y 07:11) frente a decenas durante el día anterior.

### 7.3 Cambios al protocolo/código no protegido (declarar en el reporte)
1. **Plan ampliado de 144 a 180 corridas** (`orchestrator/state.py`, commit `36cb54a`; copia del plan original en `state/plan.json.bak-144`): +36 corridas `relaxed` de Q2 en los perfiles A y B, añadidas **al final** sin alterar el orden de las 144 originales.
2. **`MAX_INCIDENTS` 4 → 12** (`orchestrator/incident.py`): tope operativo, no afecta a la medición.
3. Las 144 corridas originales y las 36 añadidas se midieron con el mismo código protegido (hash del manifiesto invariable).

### 7.4 Hallazgos clave para el reporte (resultados reproducibles, 3 de 3 repeticiones)
- **PostgreSQL, Q1, c=50, `strict`:** `oom` en los perfiles **B (2 GiB) y C (1 GiB)** (6 corridas `oom` en la matriz principal); en el perfil A (4 GiB) termina en `spill`. MongoDB completa la misma celda en A, B y C.
- **`relaxed` (`work_mem` 64 MB), perfil C, Q1:** c=1 → `spill` (mejor QPS que `strict`, ≈ 0.177 vs ≈ 0.145); **c=10 → `oom`** (`strict`: `spill`); c=50 → `oom` (igual que `strict`). Respalda la hipótesis de efecto opuesto de `work_mem` según la concurrencia (reporte §4.2). Las 6 filas `oom` del anexo son PostgreSQL/C/Q1 con c=10 y c=50.
- **Q2 (Top-N):** sin `spill` ni `oom` en ninguna configuración de ambos motores.
- **Q2/c=1/C:** MongoDB (≈ 0.2 QPS) más lento que PostgreSQL (≈ 1.1–1.3 QPS), opuesto a Q1.

### 7.5 Siguientes pasos sugeridos
1. Ejecutar `./.venv/Scripts/python.exe analysis/analyze_campaign.py` para generar tablas y figuras (ya existen `results/analysis/tables_summary.md` y `summary_metrics.json` de una ejecución previa; **regenerar** con los 180 resultados) y poblar §3, §4 y §6 de `docs/reporte_tecnico_benchmark.md`.
2. Actualizar las secciones 2.3 y 7 del reporte con las ampliaciones y las limitaciones de §7.2–7.3.
3. Hacer commit de `results/`, `analysis/` y `docs/` (hoy sin versionar) y guardar copia de `results/raw/` fuera del repo.
4. Apagar el stack: `docker compose --env-file profiles/A.env stop` (los contenedores de telemetría ya están detenidos). **No** borrar volúmenes hasta tener el análisis cerrado.

## 7. CIERRE — Campaña terminada (domingo 2026-11-01 14:44)

### Resultado
- `journal`: evento `done` a las 14:44:22 (ts 1793565862); **180/180 corridas** (144 originales + 36 ampliadas). Duración total ≈ 36 h 40 min (inicio 2026-10-31 02:02), de las que ≈ 3 h 35 min fueron una parada por el atasco del motor Docker (18:22–21:57) y ≈ 5 h otras paradas/reintentos al inicio (03:25–09:35).
- **Matriz principal** (`results/mediciones.csv`): 108 filas = **81 ok + 21 spill + 6 oom**.
- **Anexo `relaxed`** (`results/anexo_variantes.csv`): 72 filas = **63 ok + 3 spill + 6 oom**.
- Todos los estados `spill` y `oom` son **resultados experimentales legítimos** (M2), reproducidos de forma consistente entre repeticiones: OOM de PostgreSQL en Q1 con c=50 en perfiles B y C (r1–r3) y, en el anexo `work_mem=64MB`, OOM en perfil C con c=10 y c=50 (r1–r3). Ningún `run_finished` terminó con estado de fallo.
- **Integridad:** `manifest.changed() = []` (ningún archivo protegido modificado); `doctor` ✓ en cada reanudación; datos (`results/raw/`, journal, plan) sin edición manual.

### Intervenciones operativas (para declarar en el reporte; ninguna altera las mediciones)
| Qué | Detalle |
| :--- | :--- |
| Ampliación del plan | 144 → 180 corridas (36 `relaxed` Q2 en perfiles A y B al final del plan; las 144 originales intactas) — sesión previa, 10:30 del 31/10 |
| `MAX_INCIDENTS` | 4 → 12 en `orchestrator/incident.py` (no protegido) — 12:44 del 31/10 |
| Reinicios de Docker/WSL2 | 16 reinicios preventivos/correctivos con pausa limpia (`pause` → reinicio → `doctor` → `resume`), ninguna corrida registrada perdida |
| Corridas descartadas | 31 `run_discarded` (`telemetry_gap` mayoritariamente; también `runner_exception`, `compose_failed`, `drop_caches_failed`, `engine_start_failed`), siempre repetidas desde cero; no figuran en los CSV |
| Incidencias | 9 en total (de las 12 permitidas) |
| Watchdog | Reiniciado 3 veces; `claude` añadido al PATH de usuario; tope subido a 6 autocorrecciones |

### Hallazgos de infraestructura (limitaciones del estudio)
1. Docker Desktop/WSL2 se degrada tras ≈ 1–1.5 h de uso continuo (lecturas lentas del cgroup, `docker` CLI lento, `drop_caches` fallido) → mitigado con reinicios cada ≈ 60–75 min.
2. Descartes esporádicos por `telemetry_gap` con Docker sano; evidencia (`cgroup_window_samples = 0`) compatible con el defecto de `benchmark_runner/telemetry.py:156` (`_cpu_times` ante líneas vacías de `/proc/stat`). No se modificó el archivo (protegido); el reintento automático resolvió cada caso.
3. Un atasco del motor (error 500 durante > 3 h, 18:22–21:57) se resolvió con un reinicio limpio de Docker Desktop; causa no determinada.

### Siguientes pasos sugeridos (usuario)
- Ejecutar `./.venv/Scripts/python.exe analysis/analyze_campaign.py` para generar tablas y figuras (`results/analysis/`) e integrarlas en las secciones 3.2, 4 y 6 de `docs/reporte_tecnico_benchmark.md`.
- Declarar en la sección de limitaciones las intervenciones de arriba y que el anexo `relaxed` se amplió a Q2 en perfiles A y B.
- Supervisión automática cancelada (campaña terminada); Docker Desktop puede cerrarse.
