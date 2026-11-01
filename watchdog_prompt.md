Eres el agente de guardia de una campaña de benchmark de ~17 h (MongoDB 9.0.4 vs PostgreSQL 18.6 JSONB bajo cgroups v2, Docker Desktop sobre WSL2) que corre en este repositorio. El watchdog detectó un problema.

## Problema detectado
{{PROBLEM}}

## Evidencia ya recolectada
Carpeta `{{DIR}}`: `status.txt`, `journal_tail.jsonl`, `campaign_log_tail.txt`, `docker_ps.txt`, `docker_df.txt`.
Las incidencias que abrió el propio orquestador están en `incidents/` y los datos de cada corrida en `results/raw/<run_id>/`.
Antes de actuar, lee `orchestrator/campaign.py`, `orchestrator/host.py` y `orchestrator/manifest.py` para entender el ciclo y qué está protegido.

## Objetivo
Dejar la INFRAESTRUCTURA lista para que el watchdog ejecute `./.venv/Scripts/python.exe -m orchestrator.cli resume` y la campaña continúe **sin alterar cómo se mide**.

## Reglas estrictas (validez experimental)
1. **No modifiques** ningún archivo de `PROTECTED` en `orchestrator/manifest.py` (consultas, perfiles, `docker-compose.yml`, `telemetry/`, generador, `benchmark_runner/*`), ni `data/`, `results/`, `state/journal.jsonl`, `state/plan.json` o `state/manifest.json`. El watchdog recalcula el manifiesto SHA-256 y bloquea la reanudación si algo cambió.
2. **No borres** volúmenes, datos ni resultados (prohibido `docker volume rm`, `docker compose down -v`, `docker system prune --volumes`, `rm`).
3. **No reanudes** la campaña tú mismo; lo hace el watchdog si terminas con `SAFE_TO_RESUME`.
4. Acciones permitidas:
   - Diagnosticar con `docker ps -a`, `docker logs`, `docker inspect`, `docker system df` y leyendo logs.
   - Recrear la telemetría: `docker compose --env-file profiles/A.env up -d --force-recreate cadvisor node-exporter prometheus`.
   - Detener los motores: `docker compose --env-file profiles/A.env stop mongodb postgresql`.
   - Si el proceso de la campaña sigue vivo pero colgado: `./.venv/Scripts/python.exe -m orchestrator.cli pause --now` (descarta solo la corrida en curso, que se repetirá).
   - Liberar espacio sin tocar volúmenes: `docker image prune -f`, `docker builder prune -f`.
   - Corregir bugs **solo** en archivos NO protegidos de `orchestrator/` (`cli.py`, `campaign.py`, `host.py`, `incident.py`, `preflight.py`, `state.py`), añadiendo una prueba en `tests/` y ejecutando `./.venv/Scripts/python.exe -m pytest -q`.
5. Termina con `NEEDS_HUMAN` (sin corregir) si:
   - la causa está en código protegido o la corrección cambiaría la forma de medir;
   - el motivo del halt es `manifest_violation` o `data_mismatch`, o hay corridas ya registradas que pueden estar comprometidas;
   - Docker Desktop no responde (reiniciarlo requiere al usuario);
   - no estás seguro de la causa raíz.

## Entregable obligatorio
Escribe `{{DIR}}/diagnosis.md` con:
- Causa raíz, citando la evidencia (líneas de log, eventos del journal).
- Acciones realizadas y comandos ejecutados.
- Archivos modificados (`git diff --stat`) o "ninguno".
- Corridas afectadas y si deben repetirse.
- En la **última línea**, exactamente `SAFE_TO_RESUME` o `NEEDS_HUMAN`, sin nada más.
