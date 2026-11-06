"""Independent offline arithmetic, availability, support and prefix checks."""

import argparse
import csv
import gzip
import json
import math
from collections import defaultdict
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import run_batch as batch


def read_csv(path):
    with Path(path).open(encoding="utf8", newline="") as f:
        return list(csv.DictReader(f))


def close(a, b, tol=1e-7):
    assert math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol), (a, b)


def solve(matrix, rhs):
    """Independent normal equations with partial-pivot Gaussian elimination."""
    a = [list(row) + [b] for row, b in zip(matrix, rhs)]
    n = len(a)
    for j in range(n):
        pivot = max(range(j, n), key=lambda i: abs(a[i][j]))
        a[j], a[pivot] = a[pivot], a[j]
        assert abs(a[j][j]) > 1e-12
        scale = a[j][j]
        a[j] = [v / scale for v in a[j]]
        for i in range(n):
            if i != j:
                factor = a[i][j]
                a[i] = [v - factor * w for v, w in zip(a[i], a[j])]
    return [row[-1] for row in a]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=batch.HERE)
    ap.add_argument("--cache", type=Path, default=batch.ROOT / ".cache/btc-eth-volatility-stage01-20261103")
    ap.add_argument("--supplemental", type=Path, default=batch.ROOT / ".cache/alpha-week-2026-10-26/pas-prefix.jsonl.gz")
    args = ap.parse_args()
    manifest = json.loads((args.output / "run-manifest.json").read_text(encoding="utf8"))
    assert manifest["script_sha256"] == batch.sha(Path(batch.__file__))
    for name, digest in manifest["output_hashes"].items():
        assert batch.sha(args.output / name) == digest
    times, panel, _, audit, _ = batch.load_inputs(args.cache, args.supplemental)
    books, funding, specs, _ = batch.load_supplement(args.supplemental)
    feats, rets = {}, {}
    for mode in ("A", "B"):
        feats[mode], rets[mode] = batch.make_features(times, panel, mode)
    events = read_csv(args.output / "events.csv")
    results = read_csv(args.output / "results.csv")
    comparisons = read_csv(args.output / "model-comparison.csv")
    models = json.loads((args.output / "models.json").read_text(encoding="utf8"))
    threshold = json.loads((args.output / "thresholds.json").read_text(encoding="utf8"))
    checked = defaultdict(int)
    # Prefix invariance uses actual truncated public data; never a synthetic market substitute.
    end = next(i for i, t in enumerate(times) if t == batch.stamp("2026-10-27"))
    truncated = {s: ps[:end + 1] for s, ps in panel.items()}
    past_keys = ("btc_past15_bps", "eth_past15_bps", "btc_past60_bps", "eth_past60_bps", "eth_rv60_bps",
                 "beta", "hedge_training_n", "residual_past60_bps", "dynamic_hedge_log_level", "beta_change",
                 "dynamic_level_change", "fixed_previous_beta_level_change")
    for mode in ("A", "B"):
        prefix, _ = batch.make_features(times[:end + 1], truncated, mode)
        for before, full in zip(prefix, feats[mode]):
            assert {k: before[k] for k in past_keys} == {k: full[k] for k in past_keys}
            checked["prefix_invariant_feature_points"] += 1
    # Independent low-dimensional OLS, not the production FWL algorithm.
    grouped = defaultdict(list)
    for e in events:
        if e["hypothesis"] == "H01" and e["valid"] == "True":
            grouped[(e["config"], e["split"])].append(e)
    for config, model in models.items():
        coeffs = model["coefficients"]
        train = grouped[(config, "development")]
        if not coeffs:
            assert len(train) < 60 or len({e["utc"][:10] for e in train}) < 5
            continue
        for name, dim in (("baseline", 2), ("candidate", 3)):
            x = [[1.0, float(e["z_bps"]), float(e["x_bps"])] for e in train]
            y = [float(e["y_bps"]) for e in train]
            a = [[math.fsum(row[i] * row[j] for row in x) for j in range(dim)] for i in range(dim)]
            b = [math.fsum(row[i] * value for row, value in zip(x, y)) for i in range(dim)]
            coefficient = solve(a, b)
            for a, b in zip(coefficient, coeffs[name]):
                close(a, b, 1e-10)
            checked["independent_normal_equation_models"] += 1
    for e in comparisons:
        if e["hypothesis"] != "H01":
            continue
        model = models[e["config"]]["coefficients"]
        for name in ("baseline", "candidate"):
            c = model[name]
            pred = c[0] + c[1] * float(e["z_bps"]) + c[2] * float(e["x_bps"])
            close(pred, e[name + "_prediction_bps"], 1e-10)
            close((float(e["y_bps"]) - pred) ** 2, e[name + "_squared_error"], 1e-10)
        checked["paired_prediction_rows"] += 1
    # Direct summation and direct rolling covariance independently check prefix arithmetic.
    for e in events:
        if e["valid"] != "True":
            continue
        i, mode = int(e["i"]), e["mode"]
        if e["hypothesis"] in ("H01", "H02"):
            target = "pas:BTC" if e.get("direction") == "ETH_to_BTC" else "pas:ETH"
            direct = math.fsum(rets[mode][target][i + 1:i + 31]) * 10000
            close(direct, e["y_bps"], 1e-8)
            checked["direct_signed_forward30"] += 1
        elif e["hypothesis"] == "H03":
            pairs = [(x, y) for x, y in zip(rets[mode]["pas:BTC"][i - 1500:i - 60],
                                          rets[mode]["pas:ETH"][i - 1500:i - 60]) if x is not None and y is not None]
            mx = math.fsum(x for x, _ in pairs) / len(pairs)
            my = math.fsum(y for _, y in pairs) / len(pairs)
            beta = math.fsum((x - mx) * (y - my) for x, y in pairs) / math.fsum((x - mx) ** 2 for x, _ in pairs)
            close(beta, e["beta"], 1e-8)
            assert len(pairs) == int(e["hedge_training_n"]) >= 1296
            for key, low, high in (("x_bps", i - 59, i + 1), ("y_bps", i + 1, i + 61)):
                direct = math.fsum(y - beta * x for x, y in zip(rets[mode]["pas:BTC"][low:high],
                                                             rets[mode]["pas:ETH"][low:high])) * 10000
                close(direct, e[key], 1e-7)
            close(-batch.sign(float(e["x_bps"])) * float(e["y_bps"]) / (1 + abs(beta)), e["relative_direction_payoff_bps"])
            checked["independent_rolling_hedges_and_labels"] += 1
            if e.get("sampled_touch_pnl_usdp"):
                pnl, fee, slip = Decimal(0), Decimal(0), Decimal(0)
                for alias, s, side, weight in (
                    ("eth", "pas:ETH", -batch.sign(float(e["x_bps"])), 1 / (1 + abs(float(e["beta"])))),
                    ("btc", "pas:BTC", batch.sign(float(e["x_bps"])) * batch.sign(float(e["beta"])),
                     abs(float(e["beta"])) / (1 + abs(float(e["beta"]))))):
                    ep = Decimal(e[f"{alias}_entry_" + ("ask" if side > 0 else "bid")])
                    xp = Decimal(e[f"{alias}_exit_" + ("bid" if side > 0 else "ask")])
                    unit = Decimal(1).scaleb(-specs[s])
                    qty = (Decimal(str(25 * weight)) / ep).quantize(unit, rounding=ROUND_DOWN)
                    assert qty == Decimal(e[alias + "_quantity"])
                    entry, exit_ = batch.quote_after(books[s], int(e["t"])), batch.quote_after(books[s], int(e["t"]) + 60 * batch.M)
                    assert entry["seq"] == int(e[alias + "_entry_seq"]) and exit_["seq"] == int(e[alias + "_exit_seq"])
                    for target, quote in ((int(e["t"]), entry), (int(e["t"]) + 60 * batch.M, exit_)):
                        assert target < quote["t"] <= target + 90000
                    pnl += side * qty * (xp - ep)
                    fee += qty * (ep + xp) * Decimal(".0009")
                    slip += qty * (ep + xp) * Decimal(".0005")
                close(pnl, e["sampled_touch_pnl_usdp"], 1e-10)
                close(fee, e["conditional_fee_9bps_usdp"], 1e-10)
                close(pnl - fee - slip, e["conditional_touch_minus_fee_slip_usdp"], 1e-10)
                checked["decimal_two_leg_quote_cost_audits"] += 1
        elif e["hypothesis"] == "H04":
            rr = rets[mode]["pas:ETH"][i - 59:i + 1]
            rv = math.sqrt(math.fsum(x * x for x in rr)) * 10000
            close(rv, e["rv60_bps"], 1e-8)
            assert e["state"] == ("low" if rv <= threshold["eth_rv60_median_bps"] else "high")
            checked["independent_volatility_states"] += 1
        elif e["hypothesis"] == "H05":
            t = int(e["t"])
            known = [r for r in funding if r["symbol"] == "pas:ETH" and r["available"] <= t and r["event"] <= t]
            last = max(known, key=lambda r: r["event"])
            assert batch.utc(last["available"]) == e["funding_first_available_utc"]
            assert batch.utc(last["event"]) == e["funding_settlement_utc"]
            assert last["rate"] == float(e["funding_rate"])
            close(math.fsum(rets["A"]["pas:ETH"][i + 1:i + 721]) * 10000, e["y_bps"], 1e-8)
            checked["funding_availability_and_12h_labels"] += 1
    # Replay event eligibility without looking at any forward outcomes.
    for mode in ("A", "B"):
        for split, (lo, hi) in batch.SPLITS.items():
            eligible, previous = [], -10**30
            for f in feats[mode]:
                if not batch.stamp(lo) + 15 * batch.M <= f["t"] < batch.stamp(hi):
                    continue
                if f["btc_past15_bps"] is None or f["eth_past15_bps"] is None:
                    continue
                if abs(f["btc_past15_bps"]) >= threshold["btc_abs15_q90_bps"] and f["t"] - previous >= 45 * batch.M:
                    eligible.append(f["t"])
                    previous = f["t"]
            actual = [int(e["t"]) for e in events if e["hypothesis"] == "H02" and e["mode"] == mode and e["split"] == split]
            assert actual == eligible
            checked["future_blind_shock_schedules"] += 1
    # No fabricated executable profit and no unsupported fits.
    assert all(e.get("executable_net_usdp", "") == "" for e in events + results)
    assert all(r.get("verified_trade_n", "0") in ("", "0") for r in results)
    assert all(not r.get("p_value") and not r.get("confidence_interval") for r in results)
    assert len(events) == manifest["event_rows"] and len(results) == manifest["results_rows"]
    assert len(comparisons) == manifest["comparison_rows"]
    assert len([r for r in results if r["config"] == "FINAL"]) == 7
    with gzip.open(args.output / "points.csv.gz", "rt", encoding="utf8") as f:
        point_n = sum(1 for _ in csv.DictReader(f))
    assert point_n == 2 * len(times)
    report = dict(status="PASS", checks=dict(checked), point_rows=point_n, event_rows=len(events),
                  result_rows=len(results), comparison_rows=len(comparisons),
                  input_audit=audit, verifier_sha256=batch.sha(Path(__file__)),
                  limitation="Offline sampled evidence only. No real fills, deploy, production test or statistical significance.")
    batch.write_json(args.output / "verification.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
