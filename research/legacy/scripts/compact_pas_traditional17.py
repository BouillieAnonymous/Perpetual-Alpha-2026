"""Keep complete trades/fills; retain hourly equity points and archive full trace in local cache."""

import gzip
import hashlib
import json
from pathlib import Path

from study_pas_traditional17 import HOUR, OUT

source = OUT / "book-ledgers.jsonl.gz"
archive = Path(".cache/alpha-week-2026-10-26/traditional17-book-ledgers-full.jsonl.gz")
if archive.exists():
    raise FileExistsError("Full trace archive already exists; do not overwrite")
source.rename(archive)
records = points = kept = 0
with (
    gzip.open(archive, "rt", encoding="utf-8") as f,
    gzip.GzipFile(filename=str(source), mode="wb", mtime=0) as out,
):
    for line in f:
        r = json.loads(line)
        hourly = {}
        points += len(r["equity"])
        for row in r["equity"]:
            hourly[row["time"] // HOUR] = row
        r["equity"] = list(hourly.values())
        kept += len(r["equity"])
        r["equity_trace"] = (
            "last observed point per UTC hour; summary drawdown computed from full minute trace"
        )
        out.write((json.dumps(r, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode())
        records += 1
manifest = {
    "full_archive": str(archive),
    "full_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "compact_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "records": records,
    "minute_equity_points": points,
    "saved_equity_points": kept,
    "retained": "all signals (separate file), fills, trades, Funding cashflows and summaries unchanged",
    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
}
(OUT / "compact-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest))
