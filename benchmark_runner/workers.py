import json
import multiprocessing as mp
import os
import time

try:
    import psycopg
    import pymongo
    from pymongo.errors import ConnectionFailure, OperationFailure
except ImportError:  # Permite ejecutar tests unitarios y validate-data sin drivers instalados
    psycopg = None
    pymongo = None
    ConnectionFailure = OperationFailure = Exception

from .classify import error_kind
from .config import (MONGO_DB, MONGO_URI, PG_DSN, ROOT, RUNNER_CPUS,
                     SESSION_STATEMENT_TIMEOUT_MS, VARIANTS)

CTX = mp.get_context("fork" if "fork" in mp.get_all_start_methods() else "spawn")


def load_query(engine, query):
    suffix = "mongo.json" if engine == "mongodb" else "pg.sql"
    text = (ROOT / "queries" / f"{query.lower()}.{suffix}").read_text(encoding="utf-8")
    return json.loads(text) if engine == "mongodb" else text


def variant_options(engine, query, variant):
    return dict(VARIANTS[variant][engine])


class MongoSession:
    engine = "mongodb"

    def __init__(self, query, variant):
        self.pipeline = load_query("mongodb", query)
        self.options = variant_options("mongodb", query, variant)
        self.client = None

    def connect(self):
        self.client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, maxPoolSize=1)
        self.client.admin.command("ping")

    def conn_needed(self):
        return self.client is None

    def execute(self, timeout_s):
        coll = self.client[MONGO_DB]["orders"]
        max_ms = max(1, min(SESSION_STATEMENT_TIMEOUT_MS, int(timeout_s * 1000)))
        return len(list(coll.aggregate(self.pipeline, maxTimeMS=max_ms, **self.options)))

    def close(self):
        if self.client:
            self.client.close()
            self.client = None

    @staticmethod
    def classify(exc):
        if isinstance(exc, ConnectionFailure):
            return "conn_lost", None
        if isinstance(exc, OperationFailure):
            return error_kind("mongodb", exc.code), exc.code
        return "other", type(exc).__name__


class PostgresSession:
    engine = "postgresql"

    def __init__(self, query, variant):
        self.sql = load_query("postgresql", query)
        self.options = variant_options("postgresql", query, variant)
        self.conn = None

    def connect(self):
        self.conn = psycopg.connect(PG_DSN, autocommit=True, prepare_threshold=None, connect_timeout=5)
        work_mem = self.options.get("work_mem", "4MB")
        self.conn.execute(f"SET work_mem = '{work_mem}'")
        self.conn.execute(f"SET statement_timeout = {SESSION_STATEMENT_TIMEOUT_MS}")

    def conn_needed(self):
        return self.conn is None or self.conn.closed

    def execute(self, timeout_s):
        # Un único round-trip al servidor: ejecuta exclusivamente la consulta SQL del benchmark
        return len(self.conn.execute(self.sql).fetchall())

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    @staticmethod
    def classify(exc):
        if isinstance(exc, psycopg.Error):
            state = exc.sqlstate
            if state is None:
                return "conn_lost", None
            return error_kind("postgresql", state), state
        return "other", type(exc).__name__


SESSIONS = {"mongodb": MongoSession, "postgresql": PostgresSession}


def _pin_runner_cpus():
    if hasattr(os, "sched_setaffinity"):
        try:
            avail = os.sched_getaffinity(0)
            target = RUNNER_CPUS & avail
            if target:
                os.sched_setaffinity(0, target)
        except OSError:
            pass


def worker(run, out_path, barrier, t_measure, t_end):
    _pin_runner_cpus()
    session = SESSIONS[run["engine"]](run["query"], run["variant"])
    with open(out_path, "w", encoding="utf-8") as out:
        def record(start, end, kind, code=None):
            out.write(json.dumps({
                "s": start - t_measure.value,
                "e": end - t_measure.value,
                "k": kind,
                "c": code,
            }) + "\n")
            out.flush()

        try:
            session.connect()
        except Exception as exc:
            kind, code = session.classify(exc)
            barrier.wait()
            barrier.wait()
            now = time.monotonic()
            record(now, now, kind, code)
            return

        barrier.wait()
        barrier.wait()
        while (now := time.monotonic()) < t_end.value:
            try:
                if session.conn_needed():
                    session.connect()
                session.execute(t_end.value - now)
                record(now, time.monotonic(), "ok")
            except Exception as exc:
                kind, code = session.classify(exc)
                record(now, time.monotonic(), kind, code)
                if kind == "conn_lost":
                    session.close()
                    time.sleep(0.5)
    session.close()
