"""Offline freeze/replay CLI. Explicit research parameters; no network or trading clients."""
import argparse
import json
from pathlib import Path

from .historical.store import HistoricalStore
from .journal import canonical
from .market_risk import MAX_BYTES, RiskConfig, freeze, replay, seal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("freeze")
    capture.add_argument("--database", required=True)
    capture.add_argument("--asof-ms", required=True, type=int)
    capture.add_argument("--symbols", required=True, nargs="+")
    capture.add_argument("--interval", required=True)
    capture.add_argument("--return-count", required=True, type=int)
    capture.add_argument("--min-samples", required=True, type=int)
    capture.add_argument("--max-age-ms", required=True, type=int)
    capture.add_argument("--source-kind", required=True, choices=("synthetic", "observed"))
    capture.add_argument("--output", required=True)
    check = commands.add_parser("replay")
    check.add_argument("--input", required=True)
    args = parser.parse_args(argv)
    if args.command == "freeze":
        config = RiskConfig(args.symbols, args.interval, args.return_count, args.min_samples, args.max_age_ms)
        store = HistoricalStore(args.database, read_only=True)
        evidence = seal(freeze(store, config, args.asof_ms, synthetic=args.source_kind == "synthetic"))
        # Never overwrite prior evidence; all computation/validation precedes creation.
        with Path(args.output).open("x", encoding="utf-8") as stream:
            stream.write(canonical(evidence) + "\n")
        result = evidence["result"]
    else:
        with Path(args.input).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Replay input byte budget exceeded")
        result = replay(json.loads(raw))
    snapshot = result["risk_snapshot"]
    print(json.dumps({"status": snapshot["status"], "sample_count": snapshot["sample_count"],
                      "snapshot_id": snapshot["snapshot_id"], "execution_authorized": False}))
    return 0 if snapshot["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
