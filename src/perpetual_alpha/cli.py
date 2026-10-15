import argparse
import json
import logging
import signal
import sqlite3
import threading
import time
from pathlib import Path

from .config import settings
from .market_data import snapshots
from .storage import Store


def status(config):
    path = Path(config["database"]).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        runtime = connection.execute("SELECT heartbeat_ms,last_success_ms,error FROM runtime").fetchone()
        rows = connection.execute(
            "SELECT symbol,count(*),max(event_ms) FROM snapshots GROUP BY symbol"
        ).fetchall()
    finally:
        connection.close()
    now = int(time.time() * 1000)
    ages = {row[0]: (now - row[2]) / 1000 for row in rows}
    healthy = bool(runtime and runtime[1] and not runtime[2])
    healthy = healthy and (now - runtime[0]) / 1000 <= max(120, config["poll_seconds"] * 3)
    healthy = healthy and all(s in ages and 0 <= ages[s] <= config["stale_seconds"] for s in config["symbols"])
    print(json.dumps({"mode": "read-only", "healthy": healthy, "runtime": runtime,
                      "samples": rows, "age_seconds": ages}, ensure_ascii=False))
    return 0 if healthy else 1


def collect(config, once=False):
    import fcntl

    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    Path(config["database"]).parent.mkdir(parents=True, exist_ok=True)
    lock = open(config["database"] + ".lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        logging.error("another_collector_is_running")
        return 1
    store = Store(config["database"])
    errors = 0
    logging.info("starting mode=read-only symbols=%s schema=1", config["symbols"])
    try:
        while not stopped.is_set():
            try:
                for row in snapshots(config["symbols"], config["stale_seconds"]):
                    store.save(row)
                    logging.info("sample symbol=%s bid=%s ask=%s event_ms=%s",
                                 row["symbol"], row["bid"], row["ask"], row["event_ms"])
                store.heartbeat(success=True)
                errors = 0
            except Exception as error:
                errors += 1
                store.heartbeat(error=type(error).__name__)
                logging.exception("collection_failed attempt=%s", errors)
                if once:
                    return 1
            if once:
                return 0
            stopped.wait(min(300, config["poll_seconds"] * 2 ** min(errors, 3)))
    finally:
        if not once:
            store.heartbeat(error="stopped")
        store.close()
        lock.close()
        logging.info("collector_stopped")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Perpetual Alpha public read-only framework")
    parser.add_argument("command", choices=["collect", "check", "status", "dry-run"])
    parser.add_argument("--config")
    parser.add_argument("--journal", default="data/dry-run/journal.sqlite3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(message)s")
    logging.Formatter.converter = time.gmtime
    if args.command == "dry-run":
        from .dry_run import print_run
        return print_run(args.config or "config/dry-run.toml", args.journal)
    config = settings(args.config or "config/collector.toml")
    return status(config) if args.command == "status" else collect(config, once=args.command == "check")


if __name__ == "__main__":
    raise SystemExit(main())
