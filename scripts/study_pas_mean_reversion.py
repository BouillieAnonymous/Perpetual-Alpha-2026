"""Offline PAS sampled-book study; never imports execution or accesses the network.

python scripts/study_pas_mean_reversion.py RAW.jsonl --output DIRECTORY
"""

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from pas_research_costs import BasisCostModel, direction


def prepare(path):
    raw = path.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    contexts, specs, quotes, rejected = {}, {}, [], Counter()
    coverage = defaultdict(list)
    for r in sorted(
        records, key=lambda r: (max(r[k] for k in ("requested_ms", "received_ms", "stored_ms")), r["seq"])
    ):
        payload = r["raw_json"]
        assert hashlib.sha256(payload.encode()).hexdigest() == r["sha256"]
        data = json.loads(payload)
        t = max(r[k] for k in ("requested_ms", "received_ms", "stored_ms"))
        s = r["symbol"]
        if r["kind"] == "metadata-context":
            for asset in data[0]["universe"]:
                assert asset["name"].startswith("pas:")
                specs[asset["name"]] = asset["szDecimals"]
        elif r["kind"] == "context" and r["quality"] == "observed":
            assert s.startswith("pas:")
            contexts[s] = (t, data["context"])
        elif r["kind"] == "book":
            coverage[s].append(t)
            if r["quality"] != "ready":
                rejected[r["quality"]] += 1
                continue
            context = contexts.get(s)
            if not context or not 0 <= t - context[0] <= 90000:
                rejected["missing-or-stale-context"] += 1
                continue
            if not 0 <= t - data["time"] <= 90000:
                rejected["stale-or-future-book"] += 1
                continue
            assert data["coin"] == s and s in specs
            bid, ask = [float(side[0]["px"]) for side in data["levels"]]
            sizes = [float(side[0]["sz"]) for side in data["levels"]]
            oracle, funding = float(context[1]["oraclePx"]), float(context[1]["funding"])
            if not 0 < bid <= ask or min(sizes) <= 0 or oracle <= 0:
                rejected["invalid-price-or-size"] += 1
                continue
            quotes.append(
                dict(
                    symbol=s,
                    t=t,
                    event=data["time"],
                    bid=bid,
                    ask=ask,
                    oracle=oracle,
                    funding=abs(funding),
                    sizes=sizes,
                    decimals=specs[s],
                    context_age_ms=t - context[0],
                    seq=r["seq"],
                )
            )
    return quotes, dict(
        input_sha256=hashlib.sha256(raw).hexdigest(),
        raw_rows=len(records),
        kinds=dict(Counter(r["kind"] for r in records)),
        rejected=dict(rejected),
        start_ms=min(r["received_ms"] for r in records),
        end_ms=max(r["received_ms"] for r in records),
        coverage={
            s: dict(book_receipts=len(ts), start_ms=min(ts), end_ms=max(ts)) for s, ts in coverage.items()
        },
        observed_size_decimals=specs,
    )


def replay(quotes, threshold=20, fee=9, slip=2, filtered=True, hold_ms=300000, signal_field=None):
    model = BasisCostModel(str(fee), str(slip), "5")
    positions, pending, latest = {}, {}, {}
    trades, counts = [], Counter()
    realized, peak, drawdown = 0.0, 0.0, 0.0

    def value(p, q):
        price = q["bid"] * (1 - slip / 10000) if p["side"] == 1 else q["ask"] * (1 + slip / 10000)
        gross_touch = p["side"] * p["qty"] * ((q["bid"] if p["side"] == 1 else q["ask"]) - p["touch"])
        gross = p["side"] * p["qty"] * (price - p["entry"])
        fees = p["qty"] * (price + p["entry"]) * fee / 10000
        funding = p["qty"] * max(p["entry"], price) * p["funding_max"] * (q["t"] - p["opened_ms"]) / 3600000
        return dict(
            net=gross - fees - funding,
            gross_touch=gross_touch,
            fee=fees,
            slippage=gross_touch - gross,
            funding_stress=funding,
            exit=price,
            closed_ms=q["t"],
        )

    def close(s, q, reason):
        nonlocal realized
        p = positions[s]
        if p["qty"] > q["sizes"][0 if p["side"] == 1 else 1]:
            counts["exit_depth_wait"] += 1
            return False
        result = value(p, q)
        trades.append(dict(symbol=s, **p, **result, reason=reason))
        realized += result["net"]
        del positions[s]
        return True

    for q in quotes:
        s, t = q["symbol"], q["t"]
        previous = latest.get(s)
        latest[s] = q
        if s in positions:
            p = positions[s]
            p["funding_max"] = max(p["funding_max"], q["funding"])
            p["mae"] = min(p["mae"], value(p, q)["net"])
            if t - p["opened_ms"] >= hold_ms:
                close(s, q, "holding-time")
        else:
            signal = (
                q[signal_field]
                if signal_field
                else direction(str(q["bid"]), str(q["ask"]), str(q["oracle"]), str(threshold))
            )
            if signal != "neutral" and filtered:
                estimate = model.estimate(
                    signal, str(q["bid"]), str(q["ask"]), str(q["oracle"]), str(q["funding"]), hold_ms
                )
                if not estimate["eligible"]:
                    counts["cost_blocked_quote_rows"] += 1
                    signal = "neutral"
            old = pending.pop(s, None)
            if (
                old
                and 1000 <= t - old[0] <= 90000
                and signal == old[1]
                and (not previous or t - previous["t"] <= 90000)
            ):
                side = 1 if signal == "up" else -1
                touch = q["ask"] if side == 1 else q["bid"]
                price = touch * (1 + side * slip / 10000)
                step = Decimal(10) ** (-q["decimals"])
                qty = float(
                    (Decimal("100") / Decimal(str(price)) / step).to_integral_value(rounding=ROUND_FLOOR)
                    * step
                )
                if qty * price < 10 or qty > q["sizes"][1 if side == 1 else 0]:
                    counts["entry_depth_or_minimum_rejected"] += 1
                else:
                    positions[s] = dict(
                        side=side,
                        qty=qty,
                        touch=touch,
                        entry=price,
                        opened_ms=t,
                        funding_max=q["funding"],
                        mae=0.0,
                        signal_ms=old[0],
                        entry_basis_bps=(q["oracle"] / touch - 1) * 10000,
                    )
                    counts["entries"] += 1
            elif signal != "neutral":
                pending[s] = (t, signal)
                counts["signals"] += 1
        equity = realized + sum(value(p, latest[symbol])["net"] for symbol, p in positions.items())
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    ordinary = len(trades)
    end_ms = quotes[-1]["t"]
    for s in list(positions):
        if end_ms - latest[s]["t"] <= 90000:
            close(s, latest[s], "sample-end-liquidation-estimate")
    per_asset = {}
    for s in sorted({q["symbol"] for q in quotes}):
        selected = [t for t in trades if t["symbol"] == s]
        per_asset[s] = dict(
            trades=len(selected),
            wins=sum(t["net"] > 0 for t in selected),
            net=sum(t["net"] for t in selected),
            gross_touch=sum(t["gross_touch"] for t in selected),
        )
    return dict(
        parameters=dict(
            threshold_bps=threshold,
            fee_bps_per_side=fee,
            slippage_bps_per_side=slip,
            cost_filter=filtered,
            hold_ms=hold_ms,
            max_notional_per_position=100,
        ),
        counters=dict(counts),
        ordinary_exits=ordinary,
        terminal_exits=len(trades) - ordinary,
        unresolved_positions=positions,
        closed_trades=len(trades),
        net=sum(t["net"] for t in trades),
        gross_touch=sum(t["gross_touch"] for t in trades),
        fees=sum(t["fee"] for t in trades),
        slippage=sum(t["slippage"] for t in trades),
        funding_stress=sum(t["funding_stress"] for t in trades),
        win_rate=sum(t["net"] > 0 for t in trades) / len(trades) if trades else None,
        worst_trade=min((t["net"] for t in trades), default=None),
        sampled_max_drawdown=drawdown,
        max_holding_minutes=max(((t["closed_ms"] - t["opened_ms"]) / 60000 for t in trades), default=None),
        by_asset=per_asset,
        trades=trades,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    quotes, manifest = prepare(args.input)
    assert quotes
    assets = {}
    for s in sorted({q["symbol"] for q in quotes}):
        qs = [q for q in quotes if q["symbol"] == s]
        assets[s] = dict(
            valid_books=len(qs),
            median_interval_seconds=statistics.median((b["t"] - a["t"]) / 1000 for a, b in zip(qs, qs[1:])),
            mean_spread_bps=statistics.mean(
                (q["ask"] - q["bid"]) / ((q["ask"] + q["bid"]) / 2) * 10000 for q in qs
            ),
            mean_basis_bps=statistics.mean(((q["bid"] + q["ask"]) / 2 / q["oracle"] - 1) * 10000 for q in qs),
        )
    experiments = {}
    for threshold in (10, 20, 50):
        for label, fee, slip, filtered in [
            ("base", 9, 2, True),
            ("unfiltered", 9, 2, False),
            ("double-cost", 18, 4, True),
            ("zero-cost", 0, 0, False),
        ]:
            experiments[f"{threshold}bps-{label}"] = replay(quotes, threshold, fee, slip, filtered)
    # Chronological robustness: fixed settings, each half starts flat.
    cutoff = (quotes[0]["t"] + quotes[-1]["t"]) // 2
    for label, subset in [
        ("first-half", [q for q in quotes if q["t"] < cutoff]),
        ("second-half", [q for q in quotes if q["t"] >= cutoff]),
    ]:
        experiments[label] = replay(subset)
        experiments[label + "-unfiltered"] = replay(subset, filtered=False)
    for minutes in (1, 15, 30, 60):
        experiments[f"20bps-unfiltered-{minutes}min"] = replay(
            quotes, filtered=False, hold_ms=minutes * 60000
        )
    result = dict(
        schema="pas-sampled-mean-reversion/1",
        manifest=manifest,
        assets=assets,
        method_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        experiments=experiments,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                manifest=manifest,
                assets=assets,
                experiments={
                    k: {
                        x: v[x]
                        for x in (
                            "closed_trades",
                            "terminal_exits",
                            "net",
                            "gross_touch",
                            "fees",
                            "slippage",
                            "funding_stress",
                            "win_rate",
                            "worst_trade",
                            "sampled_max_drawdown",
                            "counters",
                            "unresolved_positions",
                        )
                    }
                    for k, v in experiments.items()
                },
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
