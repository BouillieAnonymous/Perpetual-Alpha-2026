"""A association, B receipt-time diagnostics, C frozen-rule signal transfer, all OFFLINE."""
import argparse
import csv
import gzip
import hashlib
import json
import statistics
from pathlib import Path

from perpetual_alpha import market_transfer
from perpetual_alpha.market_transfer import (
    association,
    input_identity,
    lead_lag_diagnostic,
    load_books,
    load_external,
    received_quote_trial,
    transfer_orders,
)
from perpetual_alpha.offline_quant.data import HOUR, read_archive, source_hash
from perpetual_alpha.offline_quant.replay import Costs, replay


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")


def execute(root, output):
    if output.exists():
        raise FileExistsError("New transfer output required; keep failed and prior runs")
    output.mkdir(parents=True)
    data_dir = root / "external-transfer"
    par, funding, specs, _ = read_archive(root / "official-hourly.jsonl.gz")
    external, evidence = load_external(data_dir / "external-history.jsonl.gz")
    minute_external, _ = load_external(data_dir / "external-minute.jsonl.gz")
    minute_par, _ = load_external(data_dir / "par-minute.jsonl.gz")
    books, failures = load_books(data_dir / "paired-book-decoded-probe.jsonl.gz")
    symbols = ("par:BTC", "par:ETH")
    association_result = {}
    for symbol in symbols:
        association_result[symbol] = {
            "hourly_seen": association(external[symbol], par[symbol], return_minutes=(60, 240, 1440)),
            "recent_minute_seen": association(minute_external[symbol], minute_par[symbol],
                                               base_interval="1m", return_minutes=(1, 5, 60))}
    save(output / "association.json", association_result)
    save(output / "lead-lag.json", lead_lag_diagnostic(books))
    quote_trials = []
    for session in sorted({b.session for b in books}):
        for symbol in symbols:
            decisions = sorted({b.available_ms for b in books if b.venue == "par" and b.symbol == symbol
                                and b.session == session})
            # True 24h lookback / 4h hold. A 2m probe must not produce invented long-horizon trades.
            for t in decisions:
                for source in ("external", "par"):
                    quote_trials.append(received_quote_trial(books, t, symbol=symbol, session=session, source=source))
    save(output / "quote-transfer.json", {"status": "INSUFFICIENT_DATA", "trials": quote_trials,
         "reason": "short concurrent probe lacks 24h signal history and 4h exits", "net_return": None})
    splits = json.loads((root / "experiments/manifest.json").read_text(encoding="utf-8"))["splits_ms"]
    # Same available history threshold for both information sources, no early external-only warmup advantage.
    warmup = max(max(par[s][0].time, external[s][0].time) + 25 * HOUR for s in symbols)
    summaries, details = [], []
    for strategy in ("tsm", "ema", "donchian"):
        for horizon in (4, 12):
            for split, (left, right) in splits.items():
                start = max(left, warmup)
                for source, inputs in (("par", par), ("external", external)):
                    orders = [o for s in symbols for o in transfer_orders(inputs[s], strategy, start, right, hold=horizon)]
                    for scenario, costs in (("official_fee_only", Costs(slippage_bps=0, spread_bps=0)),
                                            ("base", Costs()),
                                            ("stress", Costs(fee_bps=18, slippage_bps=4, spread_bps=18, delay_hours=2))):
                        result = replay({s: par[s] for s in symbols}, funding, specs, orders, start, right, costs)
                        item = {"strategy": strategy, "hold_hours": horizon, "source": source,
                                "execution_venue": "PAR-only", "split": split, "scenario": scenario,
                                "start_ms": start, "end_ms": right, "weight": .1,
                                "vintage": "RETROSPECTIVE_SEEN", **result["summary"]}
                        summaries.append(item)
                        details.append({**{k: item[k] for k in ("strategy", "hold_hours", "source", "split", "scenario")},
                                        **result})
    save(output / "summary.json", summaries)
    with gzip.open(output / "details.jsonl.gz", "wt", encoding="utf-8", newline="\n") as stream:
        for detail in details:
            stream.write(json.dumps(detail, allow_nan=False) + "\n")
    keys = {(r["strategy"], r["hold_hours"], r["source"], r["split"], r["scenario"]): r for r in summaries}
    comparisons = []
    for row in summaries:
        if row["source"] != "external":
            continue
        own = keys[row["strategy"], row["hold_hours"], "par", row["split"], row["scenario"]]
        a, b = row["net_scenario_return"], own["net_scenario_return"]
        comparisons.append({"strategy": row["strategy"], "hold_hours": row["hold_hours"],
            "split": row["split"], "scenario": row["scenario"], "par_net_scenario": b,
            "external_net_scenario": a, "external_minus_par": a - b if a is not None and b is not None else None,
            "external_trade_batches": row["trade_batches"], "par_trade_batches": own["trade_batches"],
            "external_gross_return": row["gross_return"], "par_gross_return": own["gross_return"],
            "external_max_drawdown_scenario": row["max_drawdown_scenario"],
            "par_max_drawdown_scenario": own["max_drawdown_scenario"],
            "external_beats_no_trade": a is not None and a > 0,
            "par_beats_no_trade": b is not None and b > 0,
            "executable_net_return": None, "status": "INCONCLUSIVE",
            "reason": "seen OHLC; historical availability, depth and valuation unverified"})
    save(output / "comparisons.json", comparisons)
    with (output / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(comparisons[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(comparisons)
    # External long history is a PRICE mechanism check, not proof of executable external profitability.
    long_results = []
    for s in symbols:
        series = external[s]
        opens = {r.time - HOUR: r.open for r in series}
        for strategy in ("tsm", "ema", "donchian"):
            orders = transfer_orders(series, strategy, series[0].time + 25 * HOUR, series[-1].time, hold=4)
            moves = []
            for order in orders:
                if not any(order.targets.values()):
                    continue
                if order.time + HOUR in opens and order.time + 5 * HOUR in opens:
                    direction = 1 if order.targets[s] > 0 else -1
                    moves.append(direction * (opens[order.time + 5 * HOUR] / opens[order.time + HOUR] - 1) * 10000)
            long_results.append({"symbol": s, "strategy": strategy, "observations": len(moves),
                                 "mean_direction_adjusted_price_bps": statistics.mean(moves) if moves else None,
                                 "median_bps": statistics.median(moves) if moves else None,
                                 "costs": None, "net_return": None,
                                 "status": "PRICE_DIAGNOSTIC_ONLY_SEEN"})
    save(output / "external-long-price.json", long_results)
    manifest = {"schema": "external-to-par-study/1", "external": "Hyperliquid mainnet native BTC/ETH perpetual",
        "par": "Hyperliquid testnet par:BTC/par:ETH", "sources": [
            "https://perpetual-alpha26.github.io/docs/", "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint"],
        "prior_pr_heads": {"13": "13888e7", "15": "ddfecba", "16": "5584806", "17": "0e100aa"},
        "study": "new information-source experiment; PR15 traditional rule search FROZEN",
        "parameters": {"algorithms": ["tsm24", "ema6/24", "donchian24"], "primary_hold_h": 4,
                       "secondary_prior_hold_h": 12, "fixed_per_asset_capital_fraction": .1,
                       "external_execution": False, "lag_fitting_or_time_shift": False},
        "vintage": "all downloaded historical data RETROSPECTIVE/SEEN; short probe observation only",
        "external_history": {s: {"bars": len(external[s]), "first_ms": external[s][0].time,
                                   "last_ms": external[s][-1].time} for s in symbols},
        "evidence": evidence, "probe_books": len(books), "probe_failures": len(failures),
        "inputs": {input_identity(p, root): source_hash(p) for p in [
            root / "official-hourly.jsonl.gz", root / "experiments/manifest.json",
            data_dir / "external-history.jsonl.gz", data_dir / "external-minute.jsonl.gz",
            data_dir / "par-minute.jsonl.gz", data_dir / "paired-book-decoded-probe.jsonl.gz"]},
        "code": {"market_transfer_sha256": hashlib.sha256(Path(
            market_transfer.__file__).read_text(encoding="utf-8").encode()).hexdigest(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode()).hexdigest()},
        "confirmatory_result": "INSUFFICIENT_DATA", "executable_net_return": None,
        "independent_days_not_snapshots": True}
    save(output / "manifest.json", manifest)
    save(output / "hashes.json", {p.name: source_hash(p) for p in output.iterdir() if p.is_file()})
    print(json.dumps({"scenario_runs": len(summaries), "paired_comparisons": len(comparisons),
                      "external_bars": {s: len(external[s]) for s in symbols}, "probe_books": len(books)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("docs/research/new-alpha-discovery-2026-10-18"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.root, args.output)
