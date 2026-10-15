"""Durable, single-process SIMULATION journal and execution state, separate from collector DB."""
import hashlib
import json
import sqlite3
from pathlib import Path

from .interfaces import ExecutionItem, ExecutionReport, decimal_string, identity, timestamp


def canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(payload):
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def report_from_dict(payload):
    return ExecutionReport(**{**payload, "items": tuple(
        ExecutionItem(i["symbol"], decimal_string(i["target_quantity"]),
                      decimal_string(i["actual_quantity"]) if i["actual_quantity"] is not None else None,
                      decimal_string(i["filled_quantity"]), tuple(i["order_ids"]), i["error"])
        for i in payload["items"])})


class Journal:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY, observed_ms INTEGER NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY, payload TEXT NOT NULL, report TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS report_sequences (
                decision_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS simulation_state (
                id INTEGER PRIMARY KEY CHECK(id=1), account TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts (
                kind TEXT NOT NULL, artifact_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(kind, artifact_id));
        """)
        self.connection.commit()

    def record(self, kind, payload, observed_ms):
        identity(kind)
        timestamp(observed_ms)
        self.connection.execute("INSERT INTO events(observed_ms,kind,payload) VALUES(?,?,?)",
                                (observed_ms, kind, canonical({"mode": "research" if kind.startswith("research-")
                                                               else "dry-run", "simulated": True,
                                                               "payload": payload})))

    def append(self, kind, payload, observed_ms):
        with self.connection:
            self.record(kind, payload, observed_ms)

    def events(self):
        return [{"sequence": seq, "observed_ms": time, "kind": kind, **json.loads(payload)}
                for seq, time, kind, payload in self.connection.execute(
                    "SELECT sequence,observed_ms,kind,payload FROM events ORDER BY sequence")]

    def reports(self, asof_ms):
        # Use report event history, not current decision row: later updates must not leak backwards.
        return [json.loads(payload)["payload"] for payload, in self.connection.execute(
            "SELECT payload FROM events WHERE kind='execution-report' AND observed_ms<=? ORDER BY sequence",
            (asof_ms,))]

    def close(self):
        self.connection.close()
