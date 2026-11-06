"""Export an existing collector DB read-only; run where the DB is accessible.

Usage: python scripts/export_basis_history.py /path/to/market.sqlite3 > quotes.jsonl
"""
import json
import sqlite3
import sys
from pathlib import Path

with sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + "?mode=ro", uri=True) as connection:
    for s, e, r, b, a, o, f, raw in connection.execute(
        "SELECT symbol,event_ms,received_ms,bid,ask,oracle,funding,raw_json "
        "FROM snapshots ORDER BY received_ms,symbol,event_ms"
    ):
        row = json.loads(raw)
        print(json.dumps(dict(symbol=s, event_ms=e, received_ms=r, bid=str(b), ask=str(a),
                              oracle=str(o), funding=str(f), context_received_ms=row.get("context_received_ms"),
                              book=row.get("raw", row).get("book"))))
