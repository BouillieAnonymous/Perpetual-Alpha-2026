"""Stage 1 only: bounded read-only PAS export and offline feasibility counts.

No venue client, accounts, signing, models, fitting, or strategy backtests.
"""

import argparse
import collections
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache/btc-eth-volatility-stage01-20261103"
MINUTE = 60000
CUTOFF = 1793700000000  # 2026-11-03 10:00 UTC
SYMBOLS = ("pas:BTC", "pas:ETH")

REMOTE = r"""
import gzip,hashlib,json,pathlib,sys,datetime
from alpha_csv_export import pas_research as r
from alpha_csv_export.pas_csv_export import Reader
cutoff=1793700000000
reader=Reader('/opt/perpetual-alpha-pas-data/pas/public.sqlite3')
out=gzip.GzipFile(fileobj=sys.stdout.buffer,mode='wb',mtime=0)
def emit(x): out.write((json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())
emit({'type':'header','identity':json.loads(r.raw_identity(reader.db)),
 'source_max_seq':reader.maximum,'cutoff_ms':cutoff,
 'captured_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'pipeline_sha256':hashlib.sha256(pathlib.Path(r.__file__).read_bytes()).hexdigest(),
 'exporter':'existing read-only Reader, selected raw rows normalized by existing normalize'})
last=0;count=0;digest=hashlib.sha256();raw_digest=hashlib.sha256()
while True:
 rows=reader.query("SELECT * FROM observations WHERE seq>? AND seq<=? AND (symbol IN ('pas:BTC','pas:ETH') OR (symbol IS NULL AND kind='request-error')) ORDER BY seq LIMIT 256",(last,reader.maximum))
 if not rows:break
 last=rows[-1]['seq']
 for row in rows:
  if max(row[k] for k in ('requested_ms','received_ms','stored_ms'))>cutoff:continue
  sample,_=r.normalize(row)
  raw_digest.update((r.canonical(dict(row))+'\n').encode())
  if sample['kind']=='book' and row['kind']=='book':
   raw=json.loads(row['raw_json'])
   sample['levels_sha256']=hashlib.sha256(r.canonical(raw.get('levels')).encode()).hexdigest()
  encoded=(r.canonical(sample)+'\n').encode();digest.update(encoded);emit(sample);count+=1
reader.close()
emit({'type':'footer','rows':count,'selected_raw_rows_sha256':raw_digest.hexdigest(),'normalized_rows_sha256':digest.hexdigest()})
out.close()
"""


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False)


def utc(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).isoformat()


def fetch():
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / "normalized-pas-btc-eth.jsonl.gz"
    part = target.with_suffix(".part")
    if target.exists() or part.exists():
        raise FileExistsError("Preserve prior export; choose existing offline analyze")
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=12",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=.cache/ovh-known-hosts",
        os.environ["PAS_AUDIT_SSH_TARGET"],
        "/opt/alpha-csv-export/venv/bin/python",
        "-I",
        "-",
    ]
    with part.open("xb") as f:
        result = subprocess.run(
            command, input=REMOTE.encode(), stdout=f, stderr=subprocess.PIPE, cwd=ROOT, timeout=300
        )
    if result.returncode:
        raise RuntimeError(
            "Read-only export failed; incomplete .part retained, not analyzed: "
            + result.stderr.decode(errors="replace")[:500]
        )
    # Validate stream completeness before publishing the local research snapshot.
    with gzip.open(part, "rt", encoding="utf-8") as f:
        last = None
        for line in f:
            last = json.loads(line)
    if not last or last.get("type") != "footer":
        raise ValueError("Incomplete export")
    part.rename(target)
    print("Downloaded", target, target.stat().st_size, flush=True)


def quantiles(values):
    values = sorted(values)
    if not values:
        return None
    return {str(q): values[round((len(values) - 1) * q)] for q in (0, 0.5, 0.9, 0.95, 0.99, 1)}


def runs(mask):
    lengths = []
    n = 0
    for v in mask + [False]:
        if v:
            n += 1
        elif n:
            lengths.append(n)
            n = 0
    return {
        "count": len(lengths),
        "longest": max(lengths, default=0),
        "at_least_60": sum(n >= 60 for n in lengths),
    }


def complete(mask, i, w):
    return i >= w - 1 and all(mask[i - w + 1 : i + 1])


def analyze():
    target = CACHE / "normalized-pas-btc-eth.jsonl.gz"
    dest = OUT / "stage-01-statistics.json"
    if dest.exists():
        raise FileExistsError("Preserve frozen statistics")
    with gzip.open(target, "rt", encoding="utf-8") as f:
        header = json.loads(next(f))
        rows = []
        digest = hashlib.sha256()
        for line in f:
            r = json.loads(line)
            if r.get("type") == "footer":
                footer = r
                break
            digest.update((canonical(r) + "\n").encode())
            rows.append(r)
        else:
            raise ValueError("Footer missing")
    assert len(rows) == footer["rows"]
    assert digest.hexdigest() == footer["normalized_rows_sha256"]
    # The installed exporter module is the same source file as the local reviewed module.
    source = ROOT / ".cache/pas-launch-prep/src/perpetual_alpha/pas_research.py"
    pipeline_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    # Packaged source uses normalized LF. Check actual normalized text too.
    normalized_hash = hashlib.sha256(source.read_text(encoding="utf-8").encode()).hexdigest()
    assert header["pipeline_sha256"] in (pipeline_hash, normalized_hash), "Pipeline source mismatch"
    sys.path.insert(0, str(ROOT / ".cache/pas-launch-prep/src"))
    from perpetual_alpha import pas_research as research

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(research.SCHEMA)
    for r in rows:
        fields = [k for k in r if k != "levels_sha256"]
        db.execute(
            "INSERT INTO samples(" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")",
            [r[k] for k in fields],
        )
    start = min(r["available_ms"] for r in rows if r["symbol"] in SYMBOLS) // MINUTE * MINUTE
    panel = {s: [] for s in SYMBOLS}
    for p in research.panel(db, start, CUTOFF + MINUTE):
        if p["symbol"] in panel:
            panel[p["symbol"]].append(p)
    grid = list(range(start, CUTOFF + MINUTE, MINUTE))
    n = len(grid)
    result = {
        "header": header,
        "footer": footer,
        "file_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "grid": {"start_utc": utc(start), "end_inclusive_utc": utc(CUTOFF), "points": n},
        "assets": {},
        "global_request_errors": sum(r["symbol"] is None for r in rows),
    }
    masks = {}
    {r["seq"]: r for r in rows}
    minute_output = CACHE / "causal-minute-panel.jsonl.gz"
    if minute_output.exists():
        raise FileExistsError("Preserve minute panel")
    with gzip.open(minute_output, "wt", encoding="utf-8") as out:
        for s in SYMBOLS:
            raw = [r for r in rows if r["symbol"] == s]
            books = [r for r in raw if r["kind"] == "book"]
            actual = [r for r in books if r["event_ms"] is not None]
            raw_counts = collections.Counter((r["kind"], r["quality"]) for r in raw)
            previous = None
            raw_dups = collections.Counter()
            gaps = []
            for r in actual:
                if previous:
                    raw_dups["same_event"] += r["event_ms"] == previous["event_ms"]
                    raw_dups["backwards_event"] += r["event_ms"] < previous["event_ms"]
                    raw_dups["same_top_prices"] += (r["bid"], r["ask"]) == (previous["bid"], previous["ask"])
                    raw_dups["same_full_payload"] += r["raw_sha256"] == previous["raw_sha256"]
                    raw_dups["same_levels"] += r.get("levels_sha256") == previous.get("levels_sha256")
                    delta = r["received_ms"] - previous["received_ms"]
                    if delta > 90000:
                        gaps.append(
                            {
                                "from_utc": utc(previous["received_ms"]),
                                "to_utc": utc(r["received_ms"]),
                                "ms": delta,
                            }
                        )
                previous = r
            ps = panel[s]
            strict = []
            excluded = collections.Counter()
            valid_intervals = []
            for i, p in enumerate(ps):
                valid = p["log_return"] is not None
                if valid:
                    prev = ps[i - 1]
                    delta = p["book_event_ms"] - prev["book_event_ms"]
                    same = (p["bid"], p["ask"]) == (prev["bid"], prev["ask"])
                    excluded["unchanged_top_among_pipeline_returns"] += same
                    excluded["event_interval_outside_30_90s"] += not 30000 <= delta <= 90000
                    valid = not same and 30000 <= delta <= 90000
                    valid_intervals.append(delta)
                strict.append(valid)
                out.write(
                    canonical(
                        dict(
                            p, strict_return_valid=valid, strict_log_return=p["log_return"] if valid else None
                        )
                    )
                    + "\n"
                )
            masks[s] = {"pipeline": [p["log_return"] is not None for p in ps], "strict": strict}
            for p in ps:
                for kind in ("book", "context"):
                    if p[kind + "_available_ms"] is not None:
                        assert p[kind + "_available_ms"] <= p["decision_ms"]

            def nonnull(key):
                return sum(p[key] is not None for p in ps)

            a = {
                "raw_observations": len(raw),
                "raw_quality_counts": {"|".join(k): v for k, v in raw_counts.items()},
                "range_by_available_utc": [
                    utc(min(r["available_ms"] for r in raw)),
                    utc(max(r["available_ms"] for r in raw)),
                ],
                "raw_book_receipts": len(books),
                "raw_ready_books": sum(r["quality"] == "ready" for r in books),
                "raw_duplicates": dict(raw_dups),
                "receipt_gaps_over_90s": {
                    "count": len(gaps),
                    "largest": sorted(gaps, key=lambda g: g["ms"], reverse=True)[:8],
                },
                "valid_quote_points": sum(p["quote_valid"] for p in ps),
                "unavailable_quote_points": sum(not p["quote_valid"] for p in ps),
                "field_valid_points": {k: nonnull(k) for k in ["bid", "ask", "mid", "oracle", "mark"]},
                "pipeline_returns": sum(masks[s]["pipeline"]),
                "strict_returns": sum(strict),
                "strict_exclusions": dict(excluded),
                "pipeline_valid_event_intervals_ms": quantiles(valid_intervals),
                "request_latency_ms": quantiles([r["received_ms"] - r["requested_ms"] for r in books]),
                "store_minus_receipt_ms": quantiles([r["stored_ms"] - r["received_ms"] for r in raw]),
                "book_event_age_at_grid_ms": quantiles(
                    [p["book_event_age_ms"] for p in ps if p["quote_valid"]]
                ),
                "context_age_at_grid_ms": quantiles([p["context_age_ms"] for p in ps if p["context_valid"]]),
                "return_quantiles": quantiles([p["log_return"] for p in ps if p["log_return"] is not None]),
                "abs_return_gt_1pct": sum(
                    abs(p["log_return"]) > 0.01 for p in ps if p["log_return"] is not None
                ),
                "spread_gt_50bps": sum(p["spread_bps"] > 50 for p in ps if p["spread_bps"] is not None),
                "strict_zero_returns": sum(v and p["log_return"] == 0 for p, v in zip(ps, strict)),
                "runs": {mode: runs(mask) for mode, mask in masks[s].items()},
                "rv_complete_windows": {
                    mode: {str(w): sum(complete(mask, i, w) for i in range(n)) for w in (5, 15, 30, 60)}
                    for mode, mask in masks[s].items()
                },
            }
            result["assets"][s] = a
        result["alignment"] = {
            "both_quotes": sum(
                a["quote_valid"] and b["quote_valid"] for a, b in zip(panel[SYMBOLS[0]], panel[SYMBOLS[1]])
            ),
            "both_contexts": sum(
                a["context_valid"] and b["context_valid"]
                for a, b in zip(panel[SYMBOLS[0]], panel[SYMBOLS[1]])
            ),
            "receipt_skew_ms": quantiles(
                [
                    abs(a["book_received_ms"] - b["book_received_ms"])
                    for a, b in zip(panel[SYMBOLS[0]], panel[SYMBOLS[1]])
                    if a["quote_valid"] and b["quote_valid"]
                ]
            ),
            "event_skew_ms": quantiles(
                [
                    abs(a["book_event_ms"] - b["book_event_ms"])
                    for a, b in zip(panel[SYMBOLS[0]], panel[SYMBOLS[1]])
                    if a["quote_valid"] and b["quote_valid"]
                ]
            ),
        }
        result["h1"] = {}
        for mode in ("pipeline", "strict"):
            btc, eth = masks["pas:BTC"][mode], masks["pas:ETH"][mode]
            eligible = []
            with60 = []
            bare = []
            for i in range(n):
                future = i + 30 < n and all(eth[i + 1 : i + 31])
                b = complete(btc, i, 15) and future
                bare.append(b)
                eligible.append(b and complete(eth, i, 15))
                with60.append(b and complete(eth, i, 60))

            def counts(mask):
                return {
                    "overlapping": sum(mask),
                    "fixed_30min_label_disjoint": sum(
                        v and t % (30 * MINUTE) == 0 for t, v in zip(grid, mask)
                    ),
                    "fixed_90min_support_disjoint": sum(
                        v and t % (90 * MINUTE) == 0 for t, v in zip(grid, mask)
                    ),
                }

            result["h1"][mode] = {
                "joint_return_minutes": sum(a and b for a, b in zip(btc, eth)),
                "bare_btc15_eth_future30": counts(bare),
                "with_eth_past15": counts(eligible),
                "with_eth_past60": counts(with60),
                "by_utc_day": {},
            }
            for day in sorted({utc(t)[:10] for t in grid}):
                inds = [i for i, t in enumerate(grid) if utc(t).startswith(day)]
                result["h1"][mode]["by_utc_day"][day] = {
                    "grid_points": len(inds),
                    "joint_returns": sum(btc[i] and eth[i] for i in inds),
                    **counts([v and utc(t).startswith(day) for t, v in zip(grid, eligible)]),
                }
    # Causality regression on actual data: truncate source rows at fixed historic cutoff,
    # and compare all previously emitted panel fields, not just returns.
    cut = int(datetime(2026, 10, 27, tzinfo=timezone.utc).timestamp() * 1000)
    db.execute("DELETE FROM samples WHERE available_ms>?", (cut,))
    expected = {
        (p["symbol"], p["decision_ms"]): p for s in SYMBOLS for p in panel[s] if p["decision_ms"] <= cut
    }
    checked = 0
    for p in research.panel(db, start, cut + MINUTE):
        if p["symbol"] in SYMBOLS:
            assert p == expected[(p["symbol"], p["decision_ms"])]
            checked += 1
    result["verification"] = {
        "normalized_checksum": True,
        "pipeline_hash_match": True,
        "all_selected_available_by_decision": True,
        "future_truncation_panel_points_equal": checked,
        "future_truncation_utc": utc(cut),
    }
    result["audit_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ["grid", "alignment", "h1", "verification"]}, indent=2))
    print("Statistics saved:", dest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["fetch", "analyze"])
    args = parser.parse_args()
    fetch() if args.action == "fetch" else analyze()
