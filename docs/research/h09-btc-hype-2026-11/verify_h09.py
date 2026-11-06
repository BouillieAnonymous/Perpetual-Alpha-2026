"""Independent fixed-spec verification; no alternate hypothesis/parameter tests."""

import csv
import gzip
import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def load(p):
    return json.loads(p.read_text(encoding="utf8"))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def solve(rows, k):
    # Independent high-precision normal equations with pivoting, not production QR.
    with localcontext() as c:
        c.prec = 50
        X = [[Decimal(1)] + [Decimal(r[f"x{j}"]) for j in range(k - 1)] for r in rows]
        Y = [Decimal(r["y"]) for r in rows]
        A = [
            [sum(x[i] * x[j] for x in X) for j in range(k)] + [sum(x[i] * y for x, y in zip(X, Y))]
            for i in range(k)
        ]
        for i in range(k):
            p = max(range(i, k), key=lambda j: abs(A[j][i]))
            A[i], A[p] = A[p], A[i]
            z = A[i][i]
            assert z
            A[i] = [v / z for v in A[i]]
            for j in range(k):
                if j != i:
                    z = A[j][i]
                    A[j] = [a - z * b for a, b in zip(A[j], A[i])]
        return [float(A[i][-1]) for i in range(k)]


def main():
    freeze = load(HERE / "freeze.json")
    for p, h in freeze["files"].items():
        assert sha(HERE / Path(p)) == h
    for p, h in freeze["inputs"].items():
        assert sha(ROOT / Path(p)) == h
    result = HERE / "results"
    manifest = load(result / "run-manifest.json")
    for p, h in manifest["outputs"].items():
        assert sha(result / p) == h
    assert manifest["script_sha256"] == sha(HERE / "run_h09.py")
    with (result / "predictions.csv").open(encoding="utf8", newline="") as f:
        rows = list(csv.DictReader(f))
    coeff = load(result / "coefficients.json")
    checks = {}
    maxcoef = 0
    for mode in ["A", "B"]:
        train = [
            r
            for r in rows
            if r["mode"] == mode and r["split"] == "development" and r["score_eligible"] == "True"
        ]
        for name, k in [("baseline", 3), ("candidate", 4)]:
            independent = solve(train, k)
            err = max(abs(a - b) for a, b in zip(independent, coeff[mode][name]))
            assert err < 1e-10
            maxcoef = max(maxcoef, err)
    panel = {s: [] for s in ["pas:HYPE", "pas:ETH", "pas:BTC"]}
    with gzip.open(
        ROOT / "docs/research/pas-visual-discovery-v3/audit-verified/causal-panel.jsonl.gz", "rt"
    ) as f:
        for line in f:
            r = json.loads(line)
            if r["symbol"] in panel:
                panel[r["symbol"]].append(r)
    checked = 0
    freshzero = {s: 0 for s in panel}
    for s, ps in panel.items():
        for i, p in enumerate(ps):
            if p["log_return"] is None:
                continue
            old = ps[i - 1]
            assert (
                p["quote_valid"]
                and old["quote_valid"]
                and p["book_seq"] != old["book_seq"]
                and p["book_event_ms"] > old["book_event_ms"]
            )
            assert (
                p["book_available_ms"] <= p["decision_ms"] and old["book_available_ms"] <= old["decision_ms"]
            )
            assert math.isclose(p["log_return"], math.log(p["mid"] / old["mid"]), abs_tol=1e-14)
            if p["log_return"] == 0:
                freshzero[s] += 1
            checked += 1
    for r in rows:
        i = int(r["i"])
        key = "log_return" if r["mode"] == "A" else "strict_return"
        for j, s in enumerate(panel):
            if r[f"x{j}"]:
                v = [p[key] for p in panel[s][i - 4 : i + 1]]
                assert all(x is not None for x in v)
                assert abs(math.fsum(v) - float(r[f"x{j}"])) < 1e-14
        if r["y"]:
            v = [p[key] for p in panel["pas:HYPE"][i + 1 : i + 16]]
            assert len(v) == 15 and all(x is not None for x in v)
            assert abs(math.fsum(v) - float(r["y"])) < 1e-14
        assert (r["baseline"] != "") == (r["past_eligible"] == "True")
    # Rebuild accepted event timestamps using only BTC past data. Never consult any future label.
    from run_h09 import SPLITS

    threshold = load(result / "event-summary.json")["threshold_A_development_abs_btc5_q90"]
    with (result / "events.csv").open(encoding="utf8", newline="") as f:
        events = list(csv.DictReader(f))
    for mode, key in [("A", "log_return"), ("B", "strict_return")]:
        for split, (start, end) in SPLITS.items():
            selected = []
            last = -math.inf
            for i, p in enumerate(panel["pas:BTC"]):
                t = p["decision_ms"]
                if not start <= t < end or t - 300000 < start:
                    continue
                values = [z[key] for z in panel["pas:BTC"][i - 4 : i + 1]]
                if any(v is None for v in values):
                    continue
                if abs(math.fsum(values)) >= threshold and t - last >= 1800000:
                    selected.append(t)
                    last = t
            actual = [int(r["decision_ms"]) for r in events if r["mode"] == mode and r["split"] == split]
            assert selected == actual
    reserved = load(HERE / "reserved-data.json")
    if reserved["status"] == "RESERVED_NOT_ANALYZED":
        assert sha(ROOT / reserved["path"]) == reserved["sha256"]
    checks.update(
        passed=True,
        checked_old_panel_returns=checked,
        A_fresh_zero_counts=freshzero,
        max_coefficient_difference_vs_50digit_independent_solve=maxcoef,
        prediction_rows_checked=len(rows),
        event_selection_rebuilt_from_BTC_past_only=True,
        missing_label_predictions_retained=True,
        freeze_and_output_hashes_verified=True,
        reserved_new_prefix_hash_only=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
    )
    (HERE / "verification.json").write_text(json.dumps(checks, indent=2) + "\n", encoding="utf8")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
