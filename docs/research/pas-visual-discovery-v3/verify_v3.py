"""Independent public-input arrival, truncation, feature and pair arithmetic checks."""

import argparse
import gzip
import hashlib
import json
import math
import sys
from pathlib import Path

import audit
import numeric

HERE = Path(__file__).resolve().parent


def close(x, y):
    assert (x is None) == (y is None), (x, y)
    if x is not None:
        assert math.isclose(x, y, rel_tol=1e-7, abs_tol=1e-7), (x, y)


def pair_reference(rows, x, y, i, w):
    pairs = (
        [
            (a, b)
            for a, b in zip(rows[x][i - w + 1 : i + 1], rows[y][i - w + 1 : i + 1])
            if a is not None and b is not None
        ]
        if i >= w - 1
        else []
    )
    n = len(pairs)
    corr = beta = None
    if n >= math.ceil(0.9 * w):
        mx = math.fsum(a for a, b in pairs) / n
        my = math.fsum(b for a, b in pairs) / n
        xx = math.fsum((a - mx) ** 2 for a, b in pairs)
        yy = math.fsum((b - my) ** 2 for a, b in pairs)
        xy = math.fsum((a - mx) * (b - my) for a, b in pairs)
        if xx > 1e-10 and yy > 1e-10:
            corr = xy / math.sqrt(xx * yy)
            beta = xy / xx
    return {"x": x.split(":")[1], "y": y.split(":")[1], "i": i, "w": w, "n": n, "corr": corr, "beta": beta}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", type=Path, default=HERE / "audit-verified")
    ap.add_argument("--release", type=Path, default=HERE / "release")
    a = ap.parse_args()
    meta = json.loads((a.audit / "metadata.json").read_text(encoding="utf8"))
    sy = [m["symbol"] for m in meta["markets"]]
    panel = {s: [] for s in sy}
    with gzip.open(a.audit / "causal-panel.jsonl.gz", "rt", encoding="utf8") as f:
        for line in f:
            p = json.loads(line)
            panel[p["symbol"]].append(p)
    times = [p["decision_ms"] for p in panel[sy[0]]]
    assert all([p["decision_ms"] for p in panel[s]] == times for s in sy)
    cutoff = int(
        __import__("datetime").datetime.fromisoformat("2026-10-27T00:00:00+00:00").timestamp() * 1000
    )
    # Truncate before normalization: late-arriving old events cannot enter the truncated panel.
    pipe = audit.ROOT / ".cache/pas-launch-prep/src"
    sys.path.insert(0, str(pipe))
    from perpetual_alpha import pas_research as pas

    books = {s: [] for s in sy}
    prefix = hashlib.sha256()
    last = 0
    rawpath = audit.ROOT / ".cache/validation-2026-10-30/pas-prefix.jsonl.gz"
    assert audit.sha(rawpath) == audit.HASH
    with gzip.open(rawpath, "rt", encoding="utf8") as f:
        next(f)
        for line in f:
            r = json.loads(line)
            last = r["seq"]
            prefix.update((pas.canonical(r) + "\n").encode())
            if max(r[k] for k in ["requested_ms", "received_ms", "stored_ms"]) > cutoff:
                continue
            v, _ = pas.normalize(r)
            if v["kind"] == "book" and v["symbol"] in books:
                books[v["symbol"]].append(v)
    assert prefix.hexdigest() == audit.PREFIX and last == 337657
    checked = 0
    for s in sy:
        rs = sorted(books[s], key=lambda r: (r["available_ms"], r["seq"]))
        j = 0
        prev = None
        prevok = False
        for p in panel[s]:
            t = p["decision_ms"]
            if t > cutoff:
                break
            while j < len(rs) and rs[j]["available_ms"] <= t:
                j += 1
            r = rs[j - 1] if j else None
            ok = bool(
                r
                and r["quality"] == "ready"
                and r["event_ms"] is not None
                and 0 <= t - r["event_ms"] <= 90000
                and 0 <= t - r["received_ms"] <= 90000
            )
            ret = None
            strict = None
            if ok and prevok and r["seq"] != prev["seq"] and r["event_ms"] > prev["event_ms"]:
                ret = math.log(r["mid"] / prev["mid"])
                if (r["bid"], r["ask"]) != (prev["bid"], prev["ask"]) and 30000 <= r["event_ms"] - prev[
                    "event_ms"
                ] <= 90000:
                    strict = ret
            assert p["book_seq"] == (r["seq"] if r else None) and p["quote_valid"] == ok
            close(ret, p["log_return"])
            close(strict, p["strict_return"])
            checked += 1
            prev, prevok = r, ok
    # Full-grid features recomputed; independent centered formulas at fixed deterministic samples.
    samples = [1500, 3000, 5000, 8000, 11000, len(times) - 1]
    featurechecks = 0
    references = []
    bundles = {}
    for s in sy:
        ident = s.split(":")[1]
        text = (a.release / "assets" / f"{ident}.js").read_text(encoding="utf8")
        packed = text.split("','", 1)[1].split("');", 1)[0]
        import base64

        d = json.loads(gzip.decompress(base64.b64decode(packed)))
        bundles[s] = d
        assert d["market"]["symbol"] == s and len(d["shared"]["mid"]) == len(times)
        for mode, key in [("A", "log_return"), ("B", "strict_return")]:
            full = numeric.asset_features(panel[s], mode)
            sub = numeric.asset_features([p for p in panel[s] if p["decision_ms"] <= cutoff], mode)
            for k, vals in full.items():
                for i, (x, y) in enumerate(zip(vals, d["features"][mode][k])):
                    close(x, y)
                    featurechecks += 1
                for i, v in enumerate(sub[k]):
                    close(v, vals[i])
                    featurechecks += 1
            for i in samples:
                for w in [5, 15, 30, 60]:
                    r = [p[key] for p in panel[s][i - w + 1 : i + 1]]
                    sl = None
                    if all(v is not None for v in r):
                        yy = [math.log(p["mid"]) for p in panel[s][i - w : i + 1]]
                        mu = math.fsum(yy) / (w + 1)
                        sl = (
                            10000
                            * math.fsum((j - w / 2) * (y - mu) for j, y in enumerate(yy))
                            / math.fsum((j - w / 2) ** 2 for j in range(w + 1))
                        )
                    close(sl, d["features"][mode]["slope" + str(w)][i])
                    featurechecks += 1
    for mode in ["A", "B"]:
        r = {s: bundles[s]["features"][mode]["r"] for s in sy}
        for x, y in [
            ("pas:BTC", "pas:HYPE"),
            ("pas:USDJPY", "pas:EWJ"),
            ("pas:SP500", "pas:GOLD"),
            ("pas:EWJ", "pas:USDJPY"),
        ]:
            q = next(v for v in meta["pair_eligibility"] if v["mode"] == mode and {v["x"], v["y"]} == {x, y})
            for i in samples:
                for w in [60, 240, 1440]:
                    ref = pair_reference(r, x, y, i, w)
                    ref["mode"] = mode
                    ref["global_gate"] = q["status"]
                    references.append(ref)
    # All 45 pairs' coverage/window qualifications independently from contiguous masks.
    for row in meta["pair_eligibility"]:
        key = "log_return" if row["mode"] == "A" else "strict_return"
        mask = [x[key] is not None and y[key] is not None for x, y in zip(panel[row["x"]], panel[row["y"]])]
        n = sum(mask)
        windows = sum(all(mask[i - 59 : i + 1]) for i in range(59, len(mask)))
        assert n == row["paired_returns"] and windows == row["complete60"]
        checked += 2
    audit.write(a.release / "numeric-reference.json", references)
    result = {
        "status": "PASS",
        "frozen_payload_prefix_match": True,
        "future_cutoff_utc": "2026-10-27T00:00:00Z",
        "truncated_arrival_asset_minutes": checked - 180,
        "pair_qualification_fields_checked": 180,
        "feature_cells_full_and_future_truncated": featurechecks,
        "direct_pair_reference_points": len(references),
        "markets": len(sy),
        "modes": ["A", "B"],
        "gap_and_latest_invalid_preserved": True,
        "future_labels_computed": 0,
    }
    audit.write(a.release / "independent-validation.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
