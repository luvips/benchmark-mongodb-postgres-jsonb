"""Clasificación de resultados experimentales frente a fallas de infraestructura.

Reglas de clasificación:
- Un OOM (`oom_kills > 0`), un volcado a disco (`spill`) o un error de límite de memoria
  del motor (`QueryExceededMemoryLimitNoDiskUseAllowed` 292 en Mongo / `53200` en Postgres)
  son resultados empíricos válidos del experimento y se registran en el CSV.
- Bajo contención extrema (`PROFILE_C_SEVERE`, `c=50`), algún backend puede cerrar o
  rechazar conexiones transitoriamente (`conn_lost`) mientras otras consultas sí se
  completan o sufren errores de memoria. Solo se declara falla de infraestructura
  (`connection_lost`) cuando NO hay OOM, NO hay errores de memoria y el porcentaje de
  errores de conexión domina o impide completar muestras.
"""
from dataclasses import dataclass

MONGO_KINDS = {
    146: "memory",     # ExceededMemoryLimit
    292: "memory",     # QueryExceededMemoryLimitNoDiskUseAllowed
    16819: "memory",   # Sort exceeded memory limit
    16945: "memory",   # Exceeded memory limit for $group
    50: "cancelled",   # MaxTimeMSExpired
}
PG_KINDS = {
    "53200": "memory",      # out_of_memory
    "53100": "memory",      # disk_full (temp space exhausted)
    "57014": "cancelled",   # query_canceled (statement_timeout)
    "57P01": "conn_lost",   # admin_shutdown
    "57P02": "conn_lost",   # crash_shutdown (another backend OOM-killed)
    "57P03": "conn_lost",   # cannot_connect_now (in recovery)
}


@dataclass(frozen=True)
class Signals:
    completed: int
    censored: int
    memory_errors: int
    conn_lost: int
    other_errors: int
    oom_kills: int
    spill: bool
    telemetry_ok: bool


@dataclass(frozen=True)
class Verdict:
    fault: bool
    status: str | None
    reason: str


def error_kind(engine, code):
    table = MONGO_KINDS if engine == "mongodb" else PG_KINDS
    return table.get(code, "other")


def verdict(s: Signals) -> Verdict:
    if s.oom_kills > 0:
        return Verdict(False, "oom", "oom_kill")
    if not s.telemetry_ok:
        return Verdict(True, None, "telemetry_gap")
    if s.memory_errors > 0:
        return Verdict(False, "error", "engine_memory")
    if s.other_errors > 0 and s.completed == 0:
        return Verdict(True, None, "unexpected_engine_error")
    if s.conn_lost > 0 and s.completed == 0:
        return Verdict(True, None, "connection_lost")
    if s.completed == 0:
        if s.censored > 0:
            return Verdict(False, "error", "no_completions")
        return Verdict(True, None, "no_activity")
    if s.spill:
        return Verdict(False, "spill", "disk_spill")
    return Verdict(False, "ok", "ok")
