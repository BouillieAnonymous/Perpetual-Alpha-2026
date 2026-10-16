"""Manual public history tools. Running collect is separate from the existing quote service."""
import argparse
import signal
import threading
from contextlib import nullcontext

from ..journal import canonical
from ..runtime.process_lock import ProcessLock
from .client import PublicHistoryClient
from .contracts import INTERVALS, PRICE_UNITS
from .ingest import HistoryIngestor
from .store import HistoricalStore


def main():
    parser = argparse.ArgumentParser(description="Read-only public history; never sends exchange orders")
    parser.add_argument("command", choices=("metadata", "backfill", "query", "collect"))
    parser.add_argument("--database", required=True)
    parser.add_argument("--symbols", nargs="+", choices=tuple(PRICE_UNITS), default=["par:BTC", "par:ETH"])
    parser.add_argument("--interval", choices=tuple(INTERVALS), default="1m")
    parser.add_argument("--start-ms", type=int)
    parser.add_argument("--end-ms", type=int)
    parser.add_argument("--asof-ms", type=int)
    parser.add_argument("--snapshot-seq", type=int)
    parser.add_argument("--max-records", type=int, default=5000)
    parser.add_argument("--lookback-bars", type=int, default=120)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if len(args.symbols) != len(set(args.symbols)) or not 1 <= args.lookback_bars <= 5000 or not 30 <= args.poll_seconds <= 3600:
        parser.error("Unique symbols and bounded collection settings required")
    if args.command in {"backfill", "query"} and (args.start_ms is None or args.end_ms is None):
        parser.error("Explicit aligned --start-ms and --end-ms are required")
    if args.command != "query" and (args.asof_ms is not None or args.snapshot_seq is not None):
        parser.error("Receive/availability timestamps cannot be overridden by a collection command")
    lock = nullcontext() if args.command == "query" else ProcessLock(args.database + ".writer.lock")
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    with lock:
        store = HistoricalStore(args.database, read_only=args.command == "query")
        if args.command == "query":
            asof = args.asof_ms if args.asof_ms is not None else store.clock()
            selected = store.read_metadata(asof, max_age_ms=300000, snapshot_seq=args.snapshot_seq)
            print(canonical({"mode": "read-only", "metadata": selected,
                             "candles": [store.read_candles(s, args.interval, args.start_ms, args.end_ms, asof,
                                                           max_records=args.max_records, snapshot_seq=selected["snapshot_seq"])
                                         for s in args.symbols]}))
            return 0
        ingestor = HistoryIngestor(store, PublicHistoryClient())
        if args.command == "metadata":
            print(canonical({"mode": "read-only", "metadata": ingestor.metadata()}))
            return 0
        if args.command == "backfill":
            print(canonical({"mode": "read-only", "results": [ingestor.backfill(s, args.interval, args.start_ms, args.end_ms)
                                                               for s in args.symbols]}))
            return 0
        errors = 0
        while not stopped.is_set():
            try:
                ingestor.metadata()
                step = INTERVALS[args.interval]
                end = store.clock() // step * step
                results = [ingestor.backfill(s, args.interval, max(0, end - args.lookback_bars * step), end,
                                            stopped=stopped.is_set)
                           for s in args.symbols if not stopped.is_set()]
                print(canonical({"mode": "read-only", "status": "collected", "results": results}), flush=True)
                errors = 0
            except Exception as error:
                print(canonical({"mode": "read-only", "status": "error", "error_type": type(error).__name__}), flush=True)
                errors += 1
                if args.once:
                    return 1
            if args.once:
                return 0
            stopped.wait(min(300, args.poll_seconds * 2 ** min(errors, 3)))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
