"""Huellas SHA-256 de archivos críticos del protocolo para evitar modificaciones accidentales."""
import hashlib
import json

from benchmark_runner.config import ROOT

PROTECTED = [
    "docker-compose.yml",
    "telemetry/prometheus.yml",
    "profiles/A.env",
    "profiles/B.env",
    "profiles/C.env",
    "queries/q1.mongo.json",
    "queries/q1.pg.sql",
    "queries/q2.mongo.json",
    "queries/q2.pg.sql",
    "queries/q1_float8.pg.sql",
    "queries/setup.pg.sql",
    "synthetic_data_generator.py",
    "benchmark_runner/config.py",
    "benchmark_runner/workers.py",
    "benchmark_runner/classify.py",
    "benchmark_runner/stats.py",
    "benchmark_runner/csvout.py",
    # Código que calcula throttling, spill, RSS/huella y M3: si un agente de incidencias lo
    # modifica a mitad de campaña, manifest.changed() lo detecta y la campaña se detiene.
    "benchmark_runner/runner.py",
    "benchmark_runner/telemetry.py",
    "benchmark_runner/engine_stats.py",
]


def _sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def compute(data_path):
    hashes = {rel: _sha256(ROOT / rel) for rel in PROTECTED if (ROOT / rel).exists()}
    if data_path.exists():
        hashes[str(data_path.relative_to(ROOT))] = _sha256(data_path)
    return hashes


def save(manifest_path, data_path):
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(compute(data_path), indent=1), encoding="utf-8")


def changed(manifest_path, data_path):
    if not manifest_path.exists():
        return []
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    current = compute(data_path)
    return [k for k, v in saved.items() if current.get(k) != v]


def fingerprint(path):
    if not path.exists():
        return (0, "")
    data = path.read_bytes()
    return (len(data), hashlib.sha256(data).hexdigest())


def prefix_intact(path, fp):
    size, digest = fp
    if size == 0:
        return True
    if not path.exists() or path.stat().st_size < size:
        return False
    with path.open("rb") as f:
        head = f.read(size)
    return hashlib.sha256(head).hexdigest() == digest
