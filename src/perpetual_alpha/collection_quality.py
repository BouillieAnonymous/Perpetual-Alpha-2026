"""Read-only v2 export and per-market observation coverage (not exchange uptime)."""
import argparse
import hashlib
import json
import sqlite3
import statistics
from collections import Counter
from contextlib import closing
from pathlib import Path

from .historical.contracts import PRICE_UNITS


def distribution(values):
    values = sorted(values)
    return {"n": len(values), "min": min(values) if values else None,
            "median": statistics.median(values) if values else None,
            "p95": values[int((len(values) - 1) * .95)] if values else None,
            "max": max(values) if values else None}


def quality(rows, start_ms=None, end_ms=None, gap_ms=10000):
    result = {}
    for symbol in PRICE_UNITS:
        batch = sorted((r for r in rows if r["symbol"] == symbol), key=lambda r: r["received_ms"])
        times = [r["received_ms"] for r in batch]
        intervals = [b - a for a, b in zip(times, times[1:])]
        valid = [r for r in batch if r.get("quality", "legacy") != "invalid"]
        first = start_ms if start_ms is not None else (times[0] if times else None)
        last = end_ms if end_ms is not None else (times[-1] if times else None)
        # Grid-cell coverage is explicitly a receipt availability diagnostic, not packet loss.
        expected = (last // 5000 - first // 5000 + 1) if first is not None and last is not None else 0
        occupied = len({t // 5000 for t in times if first <= t <= last}) if expected else 0
        books = [r.get("book", r.get("raw", {}).get("book", {})) for r in batch]
        fingerprints = [hashlib.sha256(json.dumps(b, sort_keys=True).encode()).hexdigest() for b in books]
        result[symbol] = dict(rows=len(batch), valid_receipt_rows=len(valid), start_ms=times[0] if times else None,
                              end_ms=times[-1] if times else None, interval_ms=distribution(intervals),
                              gaps_over_limit=sum(x > gap_ms for x in intervals), gap_limit_ms=gap_ms,
                              grid_5s_expected=expected, grid_5s_occupied=occupied,
                              grid_5s_missing=expected - occupied,
                              grid_5s_coverage=occupied / expected if expected else None,
                              same_book_event_observations=len(batch) - len({r["event_ms"] for r in batch}),
                              repeated_book_payloads=len(fingerprints) - len(set(fingerprints)),
                              quality_counts=dict(Counter(r.get("quality", "legacy") for r in batch)),
                              quality_reasons=dict(Counter(r.get("quality_reason") for r in batch)),
                              book_age_ms=distribution([r["received_ms"] - r["event_ms"] for r in batch]),
                              context_age_ms=distribution([r["received_ms"] - r["context_received_ms"]
                                                          for r in batch if r.get("context_received_ms") is not None]),
                              bid_levels=distribution([len(b.get("levels", [[], []])[0]) for b in books]),
                              ask_levels=distribution([len(b.get("levels", [[], []])[1]) for b in books]),
                              upstream_oracle_freshness="unknown: API has no upstream source timestamp")
    return result


def export(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        messages = [json.loads(r[0]) for r in connection.execute(
            "SELECT raw_json FROM collection_events_v2 ORDER BY received_ms,session,sequence")]
        rows = [json.loads(r[0]) for r in connection.execute(
            "SELECT raw_json FROM observations_v2 ORDER BY received_ms,session,sequence")]
        metadata = [m["data"] for m in messages if m["channel"] == "metadata"]
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
    return rows, dict(schema="collection-quality/0.1", integrity=integrity,
                      channels=dict(Counter(m["channel"] for m in messages)), metadata=metadata,
                      raw_message_bytes=sum(len(json.dumps(m).encode()) for m in messages),
                      assets=quality(rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    args = parser.parse_args()
    rows, report = export(args.database)
    args.rows.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    report["rows_sha256"] = hashlib.sha256(args.rows.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
