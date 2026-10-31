import json
import os
import random
import time
from dataclasses import dataclass, field

from benchmark_runner.config import CONCURRENCIES, ENGINES, PROFILES, QUERIES, REPETITIONS

RELAXED_PROFILES = ("C",)


def _run(engine, profile, query, concurrency, rep, variant):
    suffix = "" if variant == "strict" else f"-{variant}"
    return {
        "id": f"{engine}-{profile}-{query}-c{concurrency}-r{rep}{suffix}",
        "engine": engine,
        "profile": profile,
        "query": query,
        "concurrency": concurrency,
        "rep": rep,
        "variant": variant,
    }


def build_plan(seed, repetitions=REPETITIONS):
    """Construye el plan aleatorizado por bloques de repetición:
    - 36 celdas base (variante 'strict': work_mem=4MB / allowDiskUse=false) -> mediciones.csv
    - Celdas de contraste de memoria (variante 'relaxed': work_mem=64MB / allowDiskUse=true)
      para Q1 y Q2 en Perfil C con c = 1, 10, 50 (Sección 6 del playbook: las tres
      concurrencias por escenario) -> anexo_variantes.csv (responde M2). 36 corridas.
    """
    rng = random.Random(seed)
    plan = []
    for rep in range(1, repetitions + 1):
        block = [
            _run(e, p, q, c, rep, "strict")
            for e in ENGINES for p in PROFILES for q in QUERIES for c in CONCURRENCIES
        ]
        rng.shuffle(block)
        plan += block
    relaxed = [
        _run(e, p, q, c, rep, "relaxed")
        for e in ENGINES for p in RELAXED_PROFILES for q in QUERIES for c in CONCURRENCIES
        for rep in range(1, repetitions + 1)
    ]
    rng.shuffle(relaxed)
    return plan + relaxed


class Journal:
    """Registro append-only con fsync: única fuente de verdad del estado de la campaña."""

    def __init__(self, path):
        self.path = path

    def append(self, event, **fields):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": time.time(), "event": event, **fields}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read(self):
        if not self.path.exists():
            return []
        events = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events


@dataclass
class Derived:
    state: str = "new"
    pending: list = field(default_factory=list)
    finished: set = field(default_factory=set)
    suspects: set = field(default_factory=set)
    interrupted: str | None = None
    halt_reason: str | None = None
    incidents: int = 0


STATE_BY_EVENT = {
    "campaign_started": "running",
    "resumed": "running",
    "paused": "paused",
    "halted": "halted",
    "done": "done",
}


def derive(plan, events):
    d = Derived()
    running = None
    for e in events:
        kind = e["event"]
        d.state = STATE_BY_EVENT.get(kind, d.state)
        if kind == "halted":
            d.halt_reason = e.get("reason")
        elif kind == "incident_opened":
            d.incidents += 1
        elif kind == "run_started":
            running = e["run"]
        elif kind == "run_finished":
            d.finished.add(e["run"])
            running = None
        elif kind == "run_suspect":
            d.suspects.add(e["run"])
            running = None
        elif kind == "run_discarded":
            running = None
    d.interrupted = running
    regular = [r for r in plan if r["id"] not in d.finished and r["id"] not in d.suspects]
    tail = [r for r in plan if r["id"] in d.suspects and r["id"] not in d.finished]
    d.pending = regular + tail
    return d
