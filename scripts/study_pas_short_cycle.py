"""Fixed exploratory minute-scale adaptations; protocol is stored beside the results."""

import argparse
import csv
import hashlib
import json
import statistics
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

from study_pas_mean_reversion import prepare, replay


def attach_features(quotes, raw_path):
    books = {}
    for line in raw_path.read_text(encoding="utf-8-sig").splitlines():
        row = json.loads(line)
        if row["kind"] == "book":
            books[row["seq"]] = json.loads(row["raw_json"])
    series, stamps = defaultdict(list), defaultdict(list)
    diagnostics = Counter()
    for q in quotes:
        s, t = q["symbol"], q["t"]
        for name in ("MOM5", "LOCAL-MR5", "LEAD-LAG", "BOOK-IMB"):
            q[name] = "neutral"
        q["mid"] = (q["bid"] + q["ask"]) / 2

        def past(symbol, target):
            i = bisect_right(stamps[symbol], target) - 1
            if i < 0 or target - stamps[symbol][i] > 90000:
                return None
            return series[symbol][i]

        def signal(x, threshold):
            return "up" if x >= threshold else "down" if x <= -threshold else "neutral"

        old = past(s, t - 300000)
        if old:
            q["MOM5"] = signal((q["mid"] / old["mid"] - 1) * 10000, 10)
            q["momentum_reference_ms"] = old["t"]
        else:
            diagnostics["MOM5_warmup_or_stale"] += 1
        prior = [past(s, t - j * 60000) for j in range(1, 6)]
        if all(prior):
            deviation = (q["mid"] / statistics.mean(p["mid"] for p in prior) - 1) * 10000
            q["LOCAL-MR5"] = signal(-deviation, 10)
            q["mean_reference_ms"] = [p["t"] for p in prior]
        else:
            diagnostics["LOCAL-MR5_warmup_or_stale"] += 1
        if s == "pas:ETH":
            btc = past("pas:BTC", t)
            old_btc = past("pas:BTC", btc["t"] - 60000) if btc else None
            if btc and old_btc:
                q["LEAD-LAG"] = signal((btc["mid"] / old_btc["mid"] - 1) * 10000, 5)
                q["leader_ms"] = [old_btc["t"], btc["t"]]
            else:
                diagnostics["LEAD-LAG_warmup_or_stale"] += 1
        sides = books[q["seq"]]["levels"]
        b, a = [sum(float(level["sz"]) for level in side[:5]) for side in sides]
        q["imbalance"] = (b - a) / (b + a)
        q["BOOK-IMB"] = signal(q["imbalance"], 0.2)
        for name in ("MOM5", "LOCAL-MR5", "LEAD-LAG", "BOOK-IMB"):
            if q[name] != "neutral":
                diagnostics[name + "_signal_rows"] += 1
        series[s].append(q)
        stamps[s].append(t)
    return dict(diagnostics)


def run(quotes):
    results = {}
    for strategy, hold in [
        ("BASIS", 300000),
        ("MOM5", 300000),
        ("LOCAL-MR5", 300000),
        ("LEAD-LAG", 60000),
        ("BOOK-IMB", 60000),
    ]:
        for label, fee, slip in [("base", 9, 2), ("double-cost", 18, 4), ("zero-fee-slip", 0, 0)]:
            result = replay(
                quotes,
                fee=fee,
                slip=slip,
                filtered=strategy == "BASIS",
                hold_ms=hold,
                signal_field=None if strategy == "BASIS" else strategy,
            )
            result["parameters"]["strategy"] = strategy
            results[strategy + "-" + label] = result
        cutoff = (quotes[0]["t"] + quotes[-1]["t"]) // 2
        for label, subset in [
            ("first-half", [q for q in quotes if q["t"] < cutoff]),
            ("second-half", [q for q in quotes if q["t"] >= cutoff]),
        ]:
            result = replay(
                subset,
                filtered=strategy == "BASIS",
                hold_ms=hold,
                signal_field=None if strategy == "BASIS" else strategy,
            )
            result["parameters"]["strategy"] = strategy
            results[strategy + "-" + label] = result
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    quotes, manifest = prepare(args.input)
    diagnostics = attach_features(quotes, args.input)
    experiments = run(quotes)
    spreads = defaultdict(list)
    for q in quotes:
        spreads[q["symbol"]].append((q["ask"] - q["bid"]) / q["mid"] * 10000)
    making = {
        s: dict(
            mean_spread_bps=statistics.mean(v),
            median_spread_bps=statistics.median(v),
            fraction_spread_above_6bps=sum(x > 6 for x in v) / len(v),
            mean_spread_minus_assumed_maker_roundtrip_bps=statistics.mean(v) - 6,
        )
        for s, v in spreads.items()
    }
    sources = [
        Path(__file__),
        Path(__file__).with_name("study_pas_mean_reversion.py"),
        args.output / "protocol.zh-CN.md",
        Path("scripts/pas_research_costs.py"),
    ]
    result = dict(
        schema="pas-short-cycle/1",
        manifest=manifest,
        feature_diagnostics=diagnostics,
        source_sha256={
            p.resolve().relative_to(Path.cwd()).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sources
        },
        experiments=experiments,
        market_making_feasibility=making,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    fields = [
        "closed_trades",
        "terminal_exits",
        "gross_touch",
        "fees",
        "slippage",
        "funding_stress",
        "net",
        "win_rate",
        "sampled_max_drawdown",
    ]
    with (args.output / "scenarios.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scenario"] + fields)
        for k, v in experiments.items():
            w.writerow([k] + [v[x] for x in fields])
    with (args.output / "trades.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "symbol",
            "side",
            "signal_ms",
            "opened_ms",
            "closed_ms",
            "qty",
            "touch",
            "entry",
            "exit",
            "gross_touch",
            "fee",
            "slippage",
            "funding_stress",
            "net",
            "mae",
            "reason",
        ]
        w = csv.writer(f)
        w.writerow(["scenario"] + fields)
        for k, v in experiments.items():
            for trade in v["trades"]:
                w.writerow([k] + [trade[x] for x in fields])
    print(
        json.dumps(
            {
                k: {
                    x: v[x]
                    for x in (
                        "closed_trades",
                        "terminal_exits",
                        "net",
                        "gross_touch",
                        "win_rate",
                        "sampled_max_drawdown",
                        "counters",
                    )
                }
                for k, v in experiments.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
