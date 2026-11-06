"""Independent offline Stage 1 diagnostics on the frozen actual PAS export."""

import collections
import gzip
import hashlib
import json
from datetime import datetime
from pathlib import Path

from audit_stage01 import CACHE, MINUTE, OUT, SYMBOLS, utc


def stamp(s):
    return int(datetime.fromisoformat(s + "T00:00:00+00:00").timestamp() * 1000)


def main():
    dest = OUT / "stage-01-diagnostics.json"
    if dest.exists():
        raise FileExistsError("Preserve frozen diagnostics")
    raw = []
    with gzip.open(CACHE / "normalized-pas-btc-eth.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if "type" not in row:
                raw.append(row)
    panel = {s: [] for s in SYMBOLS}
    with gzip.open(CACHE / "causal-minute-panel.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            panel[row["symbol"]].append(row)
    n = len(panel[SYMBOLS[0]])
    times = [p["decision_ms"] for p in panel[SYMBOLS[0]]]
    assert times == [p["decision_ms"] for p in panel[SYMBOLS[1]]]
    out = {"assets": {}, "fixed_segments": {}, "joint": {}}
    for s in SYMBOLS:
        ps = panel[s]
        rr = [r for r in raw if r["symbol"] == s]
        kinds = {
            k: sorted([r for r in rr if r["kind"] == k], key=lambda r: (r["available_ms"], r["seq"]))
            for k in ["book", "context"]
        }
        # Independently walk actual observation arrivals, without calling pipeline.panel.
        index = {k: 0 for k in kinds}
        latest = {k: None for k in kinds}
        for p in ps:
            t = p["decision_ms"]
            for k, rs in kinds.items():
                while index[k] < len(rs) and rs[index[k]]["available_ms"] <= t:
                    latest[k] = rs[index[k]]
                    index[k] += 1
                assert (latest[k]["seq"] if latest[k] else None) == p[k + "_seq"]
            b = latest["book"]
            ok = bool(
                b
                and b["quality"] == "ready"
                and b["event_ms"] is not None
                and 0 <= t - b["event_ms"] <= 90000
                and t - b["received_ms"] <= 90000
            )
            assert ok == p["quote_valid"]
        missing = []
        for p in ps:
            if not p["quote_valid"]:
                missing.append(
                    {
                        "utc": utc(p["decision_ms"]),
                        "seq": p["book_seq"],
                        "quality": p["book_quality"],
                        "event_age_ms": p["book_event_age_ms"],
                    }
                )
        anomalies = []
        for i, p in enumerate(ps):
            r = p["log_return"]
            if r is not None and abs(r) > 0.01:
                anomalies.append(
                    {
                        "utc": utc(p["decision_ms"]),
                        "log_return": r,
                        "seq": p["book_seq"],
                        "prior_seq": ps[i - 1]["book_seq"],
                        "bid": p["bid"],
                        "ask": p["ask"],
                        "prior_mid": ps[i - 1]["mid"],
                        "oracle": p["oracle"],
                        "mark": p["mark"],
                        "prior_oracle": ps[i - 1]["oracle"],
                        "prior_mark": ps[i - 1]["mark"],
                        "event_delta_ms": p["book_event_ms"] - ps[i - 1]["book_event_ms"],
                        "strict_valid": p["strict_return_valid"],
                    }
                )
        counts = collections.Counter()
        for i, p in enumerate(ps):
            if not i or p["log_return"] is None:
                continue
            prior = ps[i - 1]
            same = (p["bid"], p["ask"]) == (prior["bid"], prior["ask"])
            span = p["book_event_ms"] - prior["book_event_ms"]
            counts["unchanged_only"] += same and 30000 <= span <= 90000
            counts["interval_only"] += not same and not 30000 <= span <= 90000
            counts["both"] += same and not 30000 <= span <= 90000
        out["assets"][s] = {
            "invalid_quote_grid_points": missing,
            "anomalies": anomalies,
            "strict_exclusion_disjoint_counts": dict(counts),
            "raw_time_fields": {
                k: {
                    "min": min(r[k] for r in rr if r[k] is not None),
                    "max": max(r[k] for r in rr if r[k] is not None),
                }
                for k in ["event_ms", "requested_ms", "received_ms", "stored_ms", "available_ms"]
            },
        }
    for mode in ["pipeline", "strict"]:
        masks = {
            s: [
                p["strict_return_valid"] if mode == "strict" else p["log_return"] is not None
                for p in panel[s]
            ]
            for s in SYMBOLS
        }
        b, e = (masks[s] for s in SYMBOLS)
        eligible = [False] * n
        sixty = [False] * n
        for i in range(14, n - 30):
            eligible[i] = all(b[i - 14 : i + 1]) and all(e[i - 14 : i + 31])
            sixty[i] = eligible[i] and i >= 59 and all(e[i - 59 : i + 1])
        for label, a, z in [
            ("development", "2026-10-20", "2026-10-27"),
            ("validation", "2026-10-27", "2026-10-31"),
            ("late", "2026-10-31", "2026-11-03"),
        ]:
            lo, hi = stamp(a), stamp(z)
            # Full price support inside each fixed split; future label ends strictly before hi.
            idx = [i for i, t in enumerate(times) if t - 15 * MINUTE >= lo and t + 30 * MINUTE < hi]
            idx60 = [i for i, t in enumerate(times) if t - 60 * MINUTE >= lo and t + 30 * MINUTE < hi]

            def count(mask, indices):
                return {
                    "overlapping": sum(mask[i] for i in indices),
                    "fixed_30min": sum(mask[i] and times[i] % (30 * MINUTE) == 0 for i in indices),
                    "fixed_90min": sum(mask[i] and times[i] % (90 * MINUTE) == 0 for i in indices),
                }

            out["fixed_segments"].setdefault(
                label, {"start_inclusive_utc": utc(lo), "end_exclusive_utc": utc(hi)}
            )[mode] = {"with_eth_past15": count(eligible, idx), "with_eth_past60": count(sixty, idx60)}
        out["joint"][mode] = {
            "all_four_price_fields_available": sum(
                a["quote_valid"] and a["context_valid"] and b["quote_valid"] and b["context_valid"]
                for a, b in zip(*panel.values())
            )
        }
    out["verification"] = {
        "independent_arrival_walk_matches_all_panel_points": 2 * n,
        "diagnostic_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
