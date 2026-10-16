"""Local synthetic Paper operations. Never overwrite a database or grant resume permission."""
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from ..journal import canonical, digest
from .paper_core import load_paper
from .paper_engine import PaperEngine
from .store import APPLICATION_ID, SCHEMA_VERSION, RuntimeStore


def inspect(path):
    """One read transaction; no migrations, state updates, secrets or raw payloads."""
    path = Path(path).resolve(strict=True)
    c = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=5)
    try:
        c.execute("BEGIN")
        if (c.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or c.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
            raise ValueError("Expected current Paper runtime schema")
        if c.execute("PRAGMA quick_check").fetchall() != [("ok",)] or c.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("SQLite integrity check failed")
        row = c.execute("SELECT payload,digest FROM paper_configuration WHERE id=1").fetchone()
        if not row:
            raise ValueError("Not a Synthetic Paper runtime")
        if digest(json.loads(row[0])) != row[1]:
            raise ValueError("Paper configuration digest mismatch")
        meta = json.loads(c.execute("SELECT payload FROM paper_meta WHERE id=1").fetchone()[0])
        counts = {table: c.execute("SELECT count(*) FROM "+table).fetchone()[0]
                  for table in ("jobs", "paper_plans", "paper_intents", "paper_fills", "paper_checks")}
        states = {table: dict(c.execute("SELECT status,count(*) FROM "+table+" GROUP BY status"))
                  for table in ("outbox", "paper_plans", "paper_intents")}
        return {"mode": "synthetic-paper", "schema_version": SCHEMA_VERSION, "paused": meta["paused"],
                "generation": meta["generation"], "pause_epoch": meta["pause_epoch"], "reasons": meta["reasons"],
                "configuration_digest": row[1], "counts": counts, "states": states,
                "database_bytes": path.stat().st_size,
                "wal_bytes": Path(str(path)+"-wal").stat().st_size if Path(str(path)+"-wal").exists() else 0,
                "real_execution_authorized": False}
    finally:
        c.close()


def verify(path, config, *, work_directory=None):
    """Rehearse restoration in a disposable copy; original backup remains untouched."""
    with tempfile.TemporaryDirectory(prefix=".paper-restore-", dir=work_directory) as folder:
        restored = Path(folder)/"runtime.sqlite3"
        _copy_database(path, restored)
        before = inspect(restored)
        engine = PaperEngine(RuntimeStore(restored), config)
        engine.boot()  # Includes complete immutable fill/book reconciliation; always paused.
        after = inspect(restored)
        if not after["paused"] or before["counts"] != after["counts"]:
            raise ValueError("Restore changed ledger counts or failed to pause")
    return {**before, "restore_verified": True, "restored_boot_paused": True}


def _copy_database(source, destination, timeout=30):
    source = Path(source).resolve(strict=True)
    src = sqlite3.connect(source.as_uri()+"?mode=ro", uri=True, timeout=5)
    dst = sqlite3.connect(destination)
    deadline = time.monotonic()+timeout

    def progress(*_):
        if time.monotonic() > deadline:
            raise TimeoutError("Online backup exceeded bounded deadline")
    try:
        src.backup(dst, pages=256, progress=progress, sleep=.05)
        dst.execute("PRAGMA journal_mode=DELETE")  # Standalone artifact, no WAL sidecars required.
    finally:
        dst.close()
        src.close()


def backup(source, destination, config):
    """SQLite online backup includes committed WAL. Publish exclusively after restore verification."""
    source, destination = Path(source).resolve(strict=True), Path(destination).absolute()
    if source == destination or destination.exists():
        raise FileExistsError("Backup requires a new destination; never replace an active database")
    inspect(source)
    descriptor, temporary = tempfile.mkstemp(prefix=".paper-backup-", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        _copy_database(source, temporary)
        result = verify(temporary, config, work_directory=destination.parent)
        with temporary.open("r+b") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            os.fsync(stream.fileno())
        # Atomic no-clobber publication on the SAME filesystem. Hard-link support required.
        if os.name == "nt":
            os.rename(temporary, destination)  # Windows rename refuses an existing target.
        else:
            os.link(temporary, destination)
        if os.name != "nt":
            fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return {**result, "sha256": checksum, "backup_bytes": destination.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", required=True)
    p.add_argument("--paper-config", default="config/paper-execution.synthetic.json")
    p.add_argument("--work-directory", help="Writable scratch space for verify; original database is never replaced")
    commands = p.add_subparsers(dest="action", required=True)
    commands.add_parser("inspect")
    commands.add_parser("verify")
    commands.add_parser("backup").add_argument("--destination", required=True)
    args = p.parse_args()
    if args.action == "inspect":
        result = inspect(args.database)
    elif args.action == "verify":
        result = verify(args.database, load_paper(args.paper_config), work_directory=args.work_directory)
    else:
        result = backup(args.database, args.destination, load_paper(args.paper_config))
    print(canonical(result))


if __name__ == "__main__":
    main()
