import csv
import os

from .config import CSV_HEADER


def row_key(row):
    return (
        row["engine"],
        row["profile"],
        row["query_id"],
        str(row["concurrency"]),
        str(row["run_number"]),
    )


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _drop_partial_line(path):
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        with path.open("r+b") as f:
            f.truncate(data.rfind(b"\n") + 1)


def existing_keys(path):
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {row_key(r) for r in csv.DictReader(f) if r.get("status")}


def append_row(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _drop_partial_line(path)
    if row_key(row) in existing_keys(path):
        return False
    lines = "" if path.exists() and path.stat().st_size else ",".join(CSV_HEADER) + "\n"
    lines += ",".join(_cell(row[c]) for c in CSV_HEADER) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(lines)
        f.flush()
        os.fsync(f.fileno())
    return True
