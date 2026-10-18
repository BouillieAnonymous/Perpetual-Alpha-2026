"""Offline write-load probe using captured observations; never contacts the venue."""
import argparse
import gzip
import json
import time
from pathlib import Path

from perpetual_alpha.stream_collection import ObservationStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--messages", type=int, default=10000)
    args = parser.parse_args()
    if args.database.exists() or not 1 <= args.messages <= 1000000:
        parser.error("Use a new benchmark database and bounded message count")
    raw = args.input.read_bytes()
    if args.input.suffix == ".gz":
        raw = gzip.decompress(raw)
    rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    if not rows:
        parser.error("Captured rows required")
    store = ObservationStore(str(args.database))
    started = time.perf_counter()
    for index in range(args.messages):
        row = rows[index % len(rows)]
        # Conservative load: every message writes both a raw event and full joined observation.
        message = {"channel": "l2Book", "data": row["book"]}
        store.save("offline-benchmark", index, row["received_ms"], message, row)
    elapsed = time.perf_counter() - started
    store.close()
    size = args.database.stat().st_size
    print(json.dumps(dict(messages=args.messages, elapsed_s=elapsed, messages_per_s=args.messages / elapsed,
                          database_bytes=size, bytes_per_message=size / args.messages,
                          scope="local storage only, not exchange/network/VPS pressure validation")))


if __name__ == "__main__":
    main()
