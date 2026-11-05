"""Header/time-only local public cache inventory after H09 freeze; no labels."""

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
assert (HERE / "freeze.json").exists()
paths = [
    ROOT / ".cache/validation-2026-10-30/pas-prefix.jsonl.gz",
    ROOT / ".cache/alpha-week-2026-10-26/pas-prefix.jsonl.gz",
    ROOT / ".cache/btc-eth-volatility-stage01-20261103/normalized-pas-btc-eth.jsonl.gz",
    ROOT / ".cache/pas-afternoon-study/raw.jsonl",
]
result = []
for p in paths:
    by = {}
    header = None
    op = gzip.open if p.suffix == ".gz" else open
    with op(p, "rt", encoding="utf8") as f:
        for line in f:
            r = json.loads(line)
            if "seq" not in r:
                if header is None:
                    header = r
                continue
            s = r.get("symbol")
            if not s:
                continue
            t = max(r[k] for k in ["requested_ms", "received_ms", "stored_ms"])
            v = by.setdefault(s, {"rows": 0, "min_available_ms": t, "max_available_ms": t})
            v["rows"] += 1
            v["min_available_ms"] = min(v["min_available_ms"], t)
            v["max_available_ms"] = max(v["max_available_ms"], t)
    result.append(
        {
            "path": str(p.relative_to(ROOT)),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "header_identity": header.get("identity") if header else None,
            "coverage": by,
        }
    )
x = {
    "inspected_utc": datetime.now(timezone.utc).isoformat(),
    "after_freeze_sha256": hashlib.sha256((HERE / "freeze.json").read_bytes()).hexdigest(),
    "local_inputs": result,
    "local_newer_full_market_found": False,
    "later_BTC_ETH_only_not_HYPE": True,
    "remote_status": "PENDING read-only existing public Reader; no accounts or service changes",
    "no_newer_prices_returns_labels_inspected": True,
    "excluded": "test/example/synthetic SQLite fixtures and duplicate publication copies; remote public source checked separately",
}
(HERE / "new-data-inventory.json").write_text(json.dumps(x, indent=2) + "\n", encoding="utf8")
print(
    json.dumps(
        [
            {
                "path": r["path"],
                "symbols": list(r["coverage"]),
                "max_available_ms": max(v["max_available_ms"] for v in r["coverage"].values()),
            }
            for r in result
        ],
        indent=2,
    )
)
