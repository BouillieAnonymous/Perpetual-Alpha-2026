"""Offline, stdlib-only return semantics and fixed low-dimensional RV diagnostics."""

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
MINUTE = 60000
SYMBOLS = ("pas:BTC", "pas:ETH")
PR_HEAD = "8c00ec8c0201e70c22cf610c21282f50cb09d7bb"
RAW_HASH = "c606ebb4b5a273f9385438fa3c3d47e64bc341ec5107aec714e4f0233d2b63db"
PANEL_HASH = "87bced947df20fdb6d8fe87e4655cbcfb943456478bd2147885ef7d1e01cf203"
SPLITS = {
    "development": ("2026-10-20", "2026-10-27"),
    "validation": ("2026-10-27", "2026-10-31"),
    "late": ("2026-10-31", "2026-11-03"),
}


def utc(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).isoformat()


def stamp(day):
    return int(datetime.fromisoformat(day + "T00:00:00+00:00").timestamp() * 1000)


def filehash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False)


def mean(x):
    return math.fsum(x) / len(x)


def dot(x, y):
    return math.fsum(a * b for a, b in zip(x, y))


def centered(x):
    avg = mean(x)
    return [v - avg for v in x]


def corr(x, y):
    if len(x) < 2:
        return None
    a, b = centered(x), centered(y)
    denominator = math.sqrt(dot(a, a) * dot(b, b))
    return dot(a, b) / denominator if denominator else None


def ranks(x):
    result = [0.0] * len(x)
    order = sorted(range(len(x)), key=x.__getitem__)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and x[order[j]] == x[order[i]]:
            j += 1
        for k in order[i:j]:
            result[k] = (i + j - 1) / 2 + 1
        i = j
    return result


def distribution(x):
    values = sorted(v for v in x if v is not None)
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": mean(values),
        "zero_n": sum(v == 0 for v in values),
        "quantiles": {
            str(q): values[round(q * (len(values) - 1))] for q in (0, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 1)
        },
    }


def fit(rows):
    """OLS with intercept, ETH first, BTC increment via FWL residualization."""
    x, z, y = ([r[k] for r in rows] for k in ("x", "z", "y"))
    mx, mz, my = mean(x), mean(z), mean(y)
    xc, zc, yc = centered(x), centered(z), centered(y)
    zz, xx = dot(zc, zc), dot(xc, xc)
    if not zz or not xx or not dot(yc, yc):
        return None
    baseline_beta = dot(zc, yc) / zz
    projection = dot(zc, xc) / zz
    xr = [a - projection * b for a, b in zip(xc, zc)]
    yr = [a - baseline_beta * b for a, b in zip(yc, zc)]
    if dot(xr, xr) / xx < 1e-10:
        return None
    btc_beta = dot(xr, yr) / dot(xr, xr)
    eth_beta = baseline_beta - btc_beta * projection
    return {
        "baseline": {"intercept": my - baseline_beta * mz, "eth_beta": baseline_beta},
        "candidate": {
            "intercept": my - eth_beta * mz - btc_beta * mx,
            "eth_beta": eth_beta,
            "btc_beta": btc_beta,
        },
        "partial_pearson_btc_y_given_eth": corr(xr, yr),
        "predictor_vif": xx / dot(xr, xr),
    }


def predict(row, coefficients):
    return (
        coefficients["intercept"]
        + coefficients["eth_beta"] * row["z"]
        + coefficients.get("btc_beta", 0) * row["x"]
    )


def describe(rows):
    result = {
        "n": len(rows),
        "utc_days": len({utc(r["t"])[:10] for r in rows}),
        "status": "DESCRIPTIVE_ONLY",
        "p_values": None,
        "confidence_intervals": None,
    }
    if len(rows) < 10:
        result["status"] = "INSUFFICIENT DATA"
        return result
    result["distributions_percent_rv"] = {k: distribution([r[k] for r in rows]) for k in ("x", "z", "y")}
    for a, b in (("x", "y"), ("z", "y"), ("x", "z")):
        u, v = [r[a] for r in rows], [r[b] for r in rows]
        result[a + "_" + b] = {"pearson": corr(u, v), "spearman": corr(ranks(u), ranks(v))}
    result["ols"] = fit(rows)
    if result["ols"]:
        total = dot(centered([r["y"] for r in rows]), centered([r["y"] for r in rows]))
        for model in ("baseline", "candidate"):
            errors = [r["y"] - predict(r, result["ols"][model]) for r in rows]
            result["ols"][model]["in_sample_r2"] = 1 - dot(errors, errors) / total
        result["ols"]["in_sample_delta_r2"] = (
            result["ols"]["candidate"]["in_sample_r2"] - result["ols"]["baseline"]["in_sample_r2"]
        )
    else:
        result["status"] = "DEGENERATE DESIGN"
    indexed = {r["t"]: r for r in rows}
    result["y_autocorrelation_by_exact_grid_lag"] = {}
    for lag in (1, 30):
        pairs = [
            (r["y"], indexed[r["t"] - lag * MINUTE]["y"]) for r in rows if r["t"] - lag * MINUTE in indexed
        ]
        result["y_autocorrelation_by_exact_grid_lag"][str(lag)] = {
            "pairs": len(pairs),
            "pearson": corr([a for a, _ in pairs], [b for _, b in pairs]),
        }
    return result


def rolling_rv(returns, w):
    output = [None] * len(returns)
    for i in range(w - 1, len(returns)):
        block = returns[i - w + 1 : i + 1]
        if all(v is not None for v in block):
            output[i] = 100 * math.sqrt(math.fsum(v * v for v in block))
    return output


def h1_rows(times, btc15, eth15, eth30):
    return [
        {"t": t, "x": btc15[i], "z": eth15[i], "y": eth30[i + 30]}
        for i, t in enumerate(times[:-30])
        if btc15[i] is not None and eth15[i] is not None and eth30[i + 30] is not None
    ]


def subset(rows, group, step):
    if group == "all":
        selected = rows
    else:
        a, b = (stamp(v) for v in SPLITS[group])
        selected = [r for r in rows if r["t"] - 15 * MINUTE >= a and r["t"] + 30 * MINUTE < b]
    return [r for r in selected if step == 1 or r["t"] % (step * MINUTE) == 0]


def independent_returns(raw, times):
    """Walk normalized arrivals without using Stage 1 panel or importing trade code."""
    result = {}
    for s in SYMBOLS:
        books = sorted(
            (r for r in raw if r["symbol"] == s and r["kind"] == "book"),
            key=lambda r: (r["available_ms"], r["seq"]),
        )
        pos, last = 0, None
        prior, prior_ok = None, False
        returns_a, returns_b, chosen = [], [], []
        for t in times:
            while pos < len(books) and books[pos]["available_ms"] <= t:
                last = books[pos]
                pos += 1
            ok = bool(
                last
                and last["quality"] == "ready"
                and last["event_ms"] is not None
                and 0 <= t - last["event_ms"] <= 90000
                and 0 <= t - last["received_ms"] <= 90000
            )
            r = None
            strict = False
            if ok and prior_ok and last["seq"] != prior["seq"] and last["event_ms"] > prior["event_ms"]:
                r = math.log(last["mid"]) - math.log(prior["mid"])
                strict = (last["bid"], last["ask"]) != (prior["bid"], prior["ask"]) and 30000 <= last[
                    "event_ms"
                ] - prior["event_ms"] <= 90000
            returns_a.append(r)
            returns_b.append(r if strict else None)
            chosen.append(last["seq"] if last else None)
            prior, prior_ok = last, ok
        result[s] = {"A": returns_a, "B": returns_b, "chosen_seq": chosen}
    return result


def historical_comparison(train, test, predictions, mode, step, split):
    train_days = len({utc(r["t"])[:10] for r in train})
    test_days = len({utc(r["t"])[:10] for r in test})
    result = {
        "train_n": len(train),
        "eval_n": len(test),
        "train_utc_days": train_days,
        "eval_utc_days": test_days,
        "status": "INSUFFICIENT DATA",
        "p_values": None,
    }
    if len(train) < 60 or train_days < 5 or len(test) < 30 or test_days < 3:
        result["reason"] = "Fixed minimum: train 60 anchors/5 days; eval 30 anchors/3 days"
        return result
    fitted = fit(train)
    if not fitted:
        result["reason"] = "Degenerate training design"
        return result
    result.update(
        status="LIMITED HISTORICAL DIAGNOSTIC",
        coefficients=fitted,
        training_label_max_utc=utc(max(r["t"] for r in train) + 30 * MINUTE),
        evaluation_feature_min_utc=utc(min(r["t"] for r in test) - 15 * MINUTE),
        eval_times_sha256=hashlib.sha256(canonical([r["t"] for r in test]).encode()).hexdigest(),
    )
    assert max(r["t"] for r in train) + 30 * MINUTE < min(r["t"] for r in test) - 15 * MINUTE
    assert fitted == fit(train), "No refit on validation or late observations"
    for model in ("baseline", "candidate"):
        predicted = [predict(r, fitted[model]) for r in test]
        errors = [r["y"] - v for r, v in zip(test, predicted)]
        result[model] = {
            "mse": mean([e * e for e in errors]),
            "mae": mean([abs(e) for e in errors]),
            "negative_prediction_n": sum(v < 0 for v in predicted),
        }
    result["mse_skill_candidate_vs_baseline"] = 1 - result["candidate"]["mse"] / result["baseline"]["mse"]
    daily = {}
    for r in test:
        b, c = predict(r, fitted["baseline"]), predict(r, fitted["candidate"])
        predictions.append(
            {
                "mode": mode,
                "anchor_minutes": step,
                "split": split,
                "utc": utc(r["t"]),
                "eth_future30_rv_percent": r["y"],
                "baseline": b,
                "candidate": c,
            }
        )
        daily.setdefault(utc(r["t"])[:10], []).append((r["y"] - b, r["y"] - c))
    result["paired_daily_errors"] = {
        day: {
            "n": len(err),
            "baseline_mse": mean([a * a for a, _ in err]),
            "candidate_mse": mean([b * b for _, b in err]),
        }
        for day, err in daily.items()
    }
    return result


def main(args):
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    names = ["stage-02a-results.json", "stage-02a-metrics.csv", "stage-02a-predictions.csv"]
    if any((out / name).exists() for name in names):
        raise FileExistsError("Preserve existing Stage 2A outputs; choose new --output")
    pr = args.pr_input
    stats = json.loads((pr / "stage-01-statistics.json").read_text(encoding="utf-8"))
    diag = json.loads((pr / "stage-01-diagnostics.json").read_text(encoding="utf-8"))
    assert filehash(args.raw) == stats["file_sha256"] == RAW_HASH
    assert filehash(args.panel) == PANEL_HASH
    raw, digest = [], hashlib.sha256()
    with gzip.open(args.raw, "rt", encoding="utf-8") as f:
        header = json.loads(next(f))
        assert (
            header["identity"]["dex"] == "pas" and header["identity"]["source"] == "hyperliquid-testnet/pas"
        )
        for line in f:
            r = json.loads(line)
            if r.get("type") == "footer":
                footer = r
                break
            digest.update((canonical(r) + "\n").encode())
            raw.append(r)
    assert len(raw) == footer["rows"] and digest.hexdigest() == footer["normalized_rows_sha256"]
    panel = {s: [] for s in SYMBOLS}
    with gzip.open(args.panel, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            panel[r["symbol"]].append(r)
    times = [p["decision_ms"] for p in panel[SYMBOLS[0]]]
    assert times == [p["decision_ms"] for p in panel[SYMBOLS[1]]]
    assert all(b - a == MINUTE for a, b in zip(times, times[1:]))
    reconstructed = independent_returns(raw, times)
    for s in SYMBOLS:
        for i, p in enumerate(panel[s]):
            assert reconstructed[s]["A"][i] == p["log_return"]
            assert reconstructed[s]["B"][i] == p["strict_log_return"]
            assert reconstructed[s]["chosen_seq"][i] == p["book_seq"]
    result = {
        "pr36_head": PR_HEAD,
        "raw_sha256": RAW_HASH,
        "panel_sha256": PANEL_HASH,
        "source_max_seq": header["source_max_seq"],
        "cutoff_utc": utc(header["cutoff_ms"]),
        "rv_unit": "percent, sqrt(sum(log_return_squared)), not annualized",
        "splits": SPLITS,
        "method": {
            "inference": "descriptive; no p values or confidence intervals",
            "historical_guard": "60 training anchors/5 days, 30 eval anchors/3 days",
            "fit": "OLS intercept, raw RV; development only; no clipping or scaling",
        },
        "input_pr_files": {p.name: filehash(p) for p in sorted(pr.iterdir()) if p.is_file()},
        "coverage": {},
        "relationships": {},
        "historical_comparisons": {},
    }
    all_rows, rv = {}, {}
    for mode, old_mode in (("A", "pipeline"), ("B", "strict")):
        rv[mode] = {}
        result["coverage"][mode] = {}
        for s in SYMBOLS:
            r = reconstructed[s][mode]
            expected = stats["assets"][s]["pipeline_returns" if mode == "A" else "strict_returns"]
            assert sum(v is not None for v in r) == expected
            rv[mode][s] = {w: rolling_rv(r, w) for w in (5, 15, 30, 60)}
            for w in rv[mode][s]:
                assert (
                    sum(v is not None for v in rv[mode][s][w])
                    == stats["assets"][s]["rv_complete_windows"][old_mode][str(w)]
                )
            result["coverage"][mode][s] = {
                "valid_returns": expected,
                "zero_return_n": sum(v == 0 for v in r),
                "rv_distributions": {str(w): distribution(v) for w, v in rv[mode][s].items()},
            }
        rows = h1_rows(times, rv[mode][SYMBOLS[0]][15], rv[mode][SYMBOLS[1]][15], rv[mode][SYMBOLS[1]][30])
        all_rows[mode] = rows
        assert len(rows) == stats["h1"][old_mode]["with_eth_past15"]["overlapping"]
        result["relationships"][mode] = {}
        for group in ("all", *SPLITS):
            result["relationships"][mode][group] = {}
            for step in (1, 30, 90):
                selected = subset(rows, group, step)
                if group != "all":
                    key = {1: "overlapping", 30: "fixed_30min", 90: "fixed_90min"}[step]
                    assert len(selected) == diag["fixed_segments"][group][old_mode]["with_eth_past15"][key]
                result["relationships"][mode][group][str(step)] = describe(selected)
        past_complete = [
            i
            for i in range(len(times) - 30)
            if rv[mode][SYMBOLS[0]][15][i] is not None and rv[mode][SYMBOLS[1]][15][i] is not None
        ]
        result["coverage"][mode]["h1_selection"] = {
            "mature_grid_decisions": len(times) - 30,
            "past_features_complete": len(past_complete),
            "complete_with_future_label": len(rows),
            "missing_future_labels_given_past_complete": len(past_complete) - len(rows),
            "daily_eligible": dict(Counter(utc(r["t"])[:10] for r in rows)),
        }
    a_by_t = {r["t"]: r for r in all_rows["A"]}
    matched = [a_by_t[r["t"]] for r in all_rows["B"]]
    assert matched == all_rows["B"]
    result["matched_semantics"] = {
        "A_values_equal_B_on_every_B_h1_time": True,
        "h1_times": len(matched),
        "max_abs_rv_difference_percent": 0,
        "A_full_30min": describe(subset(all_rows["A"], "all", 30)),
        "A_on_B_30min": describe(subset(matched, "all", 30)),
        "B_30min": describe(subset(all_rows["B"], "all", 30)),
    }
    predictions = []
    for mode in ("A", "B"):
        result["historical_comparisons"][mode] = {}
        for step in (30, 90):
            train = subset(all_rows[mode], "development", step)
            result["historical_comparisons"][mode][str(step)] = {
                split: historical_comparison(
                    train, subset(all_rows[mode], split, step), predictions, mode, step, split
                )
                for split in ("validation", "late")
            }
    # Independent features/returns prefix test; labels after the cutoff remain unavailable.
    cut = stamp("2026-10-27")
    short_times = [t for t in times if t <= cut]
    short = independent_returns([r for r in raw if r["available_ms"] <= cut], short_times)
    for s in SYMBOLS:
        for mode in ("A", "B"):
            assert short[s][mode] == reconstructed[s][mode][: len(short_times)]
            assert rolling_rv(short[s][mode], 15) == rv[mode][s][15][: len(short_times)]
    result["verification"] = {
        "independent_reconstruction_matches_stage1_panel_points": len(times) * 2,
        "stage1_rv_and_split_counts_match": True,
        "future_truncation_utc": utc(cut),
        "future_truncation_returns_and_features_unchanged": True,
        "baseline_candidate_identical_train_and_evaluation_times": True,
        "stage1_pr_file_hashes_recorded": True,
    }
    result["script_sha256"] = filehash(Path(__file__))
    result["protocol_sha256"] = filehash(HERE / "stage-02a-protocol.md")
    (out / names[0]).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    fields = [
        "mode",
        "group",
        "anchor_minutes",
        "n",
        "status",
        "btc_future_pearson",
        "eth_future_pearson",
        "btc_eth_past_pearson",
        "btc_future_spearman",
        "partial_pearson",
        "btc_beta",
        "eth_beta",
        "delta_r2",
    ]
    with (out / names[1]).open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for mode, groups in result["relationships"].items():
            for group, steps in groups.items():
                for step, d in steps.items():
                    line = {
                        "mode": mode,
                        "group": group,
                        "anchor_minutes": step,
                        "n": d["n"],
                        "status": d["status"],
                    }
                    if "x_y" in d:
                        line.update(
                            btc_future_pearson=d["x_y"]["pearson"],
                            eth_future_pearson=d["z_y"]["pearson"],
                            btc_eth_past_pearson=d["x_z"]["pearson"],
                            btc_future_spearman=d["x_y"]["spearman"],
                        )
                    if d.get("ols"):
                        line.update(
                            partial_pearson=d["ols"]["partial_pearson_btc_y_given_eth"],
                            btc_beta=d["ols"]["candidate"]["btc_beta"],
                            eth_beta=d["ols"]["candidate"]["eth_beta"],
                            delta_r2=d["ols"]["in_sample_delta_r2"],
                        )
                    writer.writerow(line)
    with (out / names[2]).open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "mode",
                "anchor_minutes",
                "split",
                "utc",
                "eth_future30_rv_percent",
                "baseline",
                "candidate",
            ],
        )
        writer.writeheader()
        writer.writerows(predictions)
    print(
        json.dumps(
            {
                "files": names,
                "verification": result["verification"],
                "h1_rows": {k: len(v) for k, v in all_rows.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--raw",
        type=Path,
        default=ROOT / ".cache/btc-eth-volatility-stage01-20261103/normalized-pas-btc-eth.jsonl.gz",
    )
    parser.add_argument(
        "--panel",
        type=Path,
        default=ROOT / ".cache/btc-eth-volatility-stage01-20261103/causal-minute-panel.jsonl.gz",
    )
    parser.add_argument("--pr-input", type=Path, default=ROOT / ".cache/btc-eth-stage02a-pr36-input")
    parser.add_argument("--output", type=Path, default=HERE)
    main(parser.parse_args())
