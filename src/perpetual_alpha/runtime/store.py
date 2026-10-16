"""Versioned runtime DB only. Each operation owns a short, same-thread connection/transaction."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from ..journal import canonical
from .contracts import decode

APPLICATION_ID = 0x50414A31
SCHEMA_VERSION = 2


class RuntimeStore:
    def __init__(self, path, *, clock=None, timeout_ms=5000):
        if str(path) == ":memory:":
            raise ValueError("Runtime requires a durable file, not a connection-local memory DB")
        decode(int, timeout_ms)
        if not 0 < timeout_ms <= 60000:
            raise ValueError("Bounded SQLite timeout required")
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        self.timeout_ms = timeout_ms
        self._migrate()

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=self.timeout_ms / 1000, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            connection.close()

    def _migrate(self):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                # Inspect and migrate under the SAME lock, including concurrent first opens.
                application = connection.execute("PRAGMA application_id").fetchone()[0]
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if application not in (0, APPLICATION_ID) or (application == 0 and (version or tables)):
                    raise ValueError("Not an empty database or Perpetual Alpha runtime database")
                if version > SCHEMA_VERSION or (application == APPLICATION_ID and version == 0):
                    raise ValueError("Unsupported runtime schema version")
                for target, filename in ((1, "001_runtime.sql"), (2, "002_mock_http.sql")):
                    if version >= target:
                        continue
                    sql = (Path(__file__).parent / "migrations" / filename).read_text(encoding="utf-8")
                    for statement in sql.split(";"):
                        if statement.strip():
                            connection.execute(statement)
                    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version={target}")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            connection.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def transaction(self):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                # Read the trusted clock AFTER acquiring the write lock, not before waiting for it.
                now = self.clock()
                decode(int, now)
                previous = connection.execute("SELECT last_now_ms FROM runtime_meta WHERE id=1").fetchone()[0]
                if now < previous:
                    raise ValueError("Core clock moved backwards; pause and reconcile the clock")
                connection.execute("UPDATE runtime_meta SET last_now_ms=? WHERE id=1", (now,))
                yield connection, now
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def event(connection, now, kind, payload):
        # Runtime facts have no fabricated simulated=true wrapper. Never put a lease token in audit.
        connection.execute("INSERT INTO audit(observed_ms,kind,payload) VALUES(?,?,?)",
                           (now, kind, canonical(payload)))

    def audit(self):
        with self.connection() as connection:
            return [{"sequence": r[0], "observed_ms": r[1], "kind": r[2], "payload": json.loads(r[3])}
                    for r in connection.execute("SELECT * FROM audit ORDER BY sequence")]
