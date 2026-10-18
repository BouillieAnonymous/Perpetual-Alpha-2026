"""Reproduce frozen exploratory comparisons; no optimization, orders or runtime hooks."""
import argparse
import csv
import gzip
import hashlib
import importlib.metadata
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .data import HOUR, coverage, read_archive, source_hash
from .diagnostics import diagnostics
from .relative import fit_pair, funding_orders, pair_orders
from .replay import Costs, replay
from .signals import cross_section_orders, single_orders

PAIR = ("par:BTC", "par:ETH")


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_digest():
    paths = sorted(Path(__file__).parent.glob("*.py"))
    return hashlib.sha256(b"".join(p.name.encode() + p.read_bytes() for p in paths)).hexdigest()


def strategy_definitions(symbols):
    result = [{"id": "NO-TRADE", "family": "control", "symbols": list(PAIR), "kind": "none"}]
    for kind in ["donchian", "atr", "donchian_compression", "donchian_expansion",
                 "donchian_compression_expansion", "ema", "tsm", "risk_momentum", "simple_mr",
                 "rolling_z", "bollinger"]:
        family = "VEX" if kind.startswith("donchian") or kind == "atr" else (
            "MR" if kind in {"simple_mr", "rolling_z", "bollinger"} else "TSM")
        result.append({"id": kind.upper(), "family": family, "symbols": list(PAIR), "kind": kind})
    for regime in ["trend", "range", "cost", "random"]:
        result.append({"id": "REGIME-" + regime.upper(), "family": "REGIME", "symbols": list(PAIR),
                       "kind": "donchian", "regime": regime})
    for group, label, count in [(symbols, "TEN-PRICE-SCENARIO", 3), (list(PAIR), "BTC-ETH-SUBSET", 1)]:
        for variant in ["risk", "raw", "reverse", "equal", "tsm", "equal_long"]:
            result.append({"id": "XSM-" + label + "-" + variant.upper(), "family": "XSM",
                           "symbols": group, "kind": "xsm", "variant": variant, "count": count})
    for mode in ["last", "mean24", "hedged"]:
        result.append({"id": "FUNDING-" + mode.upper(), "family": "FUNDING", "symbols": list(PAIR),
                       "kind": "funding", "mode": mode})
    for kind in ["pair_ratio", "pair_coint"]:
        result.append({"id": kind.upper(), "family": "PAIR", "symbols": list(PAIR), "kind": kind})
    # Fixed sensitivity suite specified before reading PnL; report ALL variants.
    for lookback, compression in [(12, 72), (48, 168)]:
        result.append({"id": f"VEX-SENS-{lookback}-{compression}", "family": "VEX_SENSITIVITY",
                       "symbols": list(PAIR), "kind": "donchian_compression_expansion",
                       "lookback": lookback, "compression": compression})
    for symbol in PAIR:
        for kind in ["donchian", "tsm", "rolling_z"]:
            result.append({"id": kind.upper() + "-" + symbol.split(":")[1], "family": "ASSET_DIAGNOSTIC",
                           "symbols": [symbol], "kind": kind})
    for lookback in (12, 48):
        result.append({"id": f"XSM-SUBSET-SENS-{lookback}", "family": "XSM_SENSITIVITY",
                       "symbols": list(PAIR), "kind": "xsm", "variant": "risk", "count": 1,
                       "lookback": lookback})
    result.append({"id": "FUNDING-LAST-24H", "family": "FUNDING_SENSITIVITY", "symbols": list(PAIR),
                   "kind": "funding", "mode": "last", "rebalance": 24})
    return result


def orders_for(definition, bars, funding, model, start, end):
    symbols, kind = definition["symbols"], definition["kind"]
    truncated = {s: [r for r in bars[s] if r.time < end] for s in symbols}
    if kind == "none":
        return []
    if kind == "xsm":
        return cross_section_orders(truncated, symbols, definition["variant"], count=definition["count"],
                                    lookback=definition.get("lookback", 24))
    if kind == "funding":
        return funding_orders(truncated, funding, symbols, mode=definition["mode"],
                              rebalance=definition.get("rebalance", 12))
    if kind.startswith("pair_"):
        return pair_orders(truncated, symbols, model, ratio=kind == "pair_ratio",
                           gate=kind == "pair_coint", end=end)
    orders = [o for s in symbols for o in single_orders(truncated[s], kind, active_start=start,
            lookback=definition.get("lookback", 24), compression=definition.get("compression", 168),
            regime=definition.get("regime"))]
    if definition.get("regime") == "random":
        kept, filtered = {}, []
        for order in sorted(orders, key=lambda o: o.time):
            symbol = next(iter(order.targets))
            if any(order.targets.values()):
                draw = int(hashlib.sha256(f"20261018:{symbol}:{order.time}".encode()).hexdigest()[:8], 16)
                kept[symbol] = draw / 2**32 < .35
            if kept.get(symbol):
                filtered.append(order)
        return filtered
    return orders


def execute(archive, output):
    if output.exists():
        raise FileExistsError("New output directory required; keep previous runs")
    bars, funding, specs, errors = read_archive(archive)
    output.mkdir(parents=True)
    symbols = sorted(bars)
    first = max(rows[0].time for rows in bars.values())
    end = min(rows[-1].time for rows in bars.values())
    total = (end - first) // HOUR
    cut1, cut2 = first + int(.6 * total) * HOUR, first + int(.8 * total) * HOUR
    splits = {"full_seen": (first, end), "development_seen": (first, cut1),
              "validation_seen": (cut1 + 12 * HOUR, cut2), "holdout_seen": (cut2 + 12 * HOUR, end)}
    model = fit_pair(bars, PAIR, cut1)
    definitions = strategy_definitions(symbols)
    env = {}
    for name in ["numpy", "scipy", "statsmodels", "pandas", "patsy", "packaging", "python-dateutil", "tzdata", "six"]:
        try:
            env[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            env[name] = None
    version = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    manifest = {"schema": "traditional-quant-experiments/1", "input_sha256": source_hash(archive),
        "archive_name": str(archive), "code_base_commit": version, "research_source_sha256": source_digest(),
        "dependencies": env, "definitions": definitions, "splits_ms": splits,
        "vintage": "Retrospective/Seen ALL periods, NOT confirmatory out-of-sample",
        "mode": "PRICE_AND_COST_SCENARIO_ONLY", "actual_availability": "see raw received/available",
        "hypothetical_availability": "bar final at close; execute next hour open, not historical receive replay",
        "parameters": {"hold_hours": 12, "lookback_hours": 24, "compression_hours": 168,
                       "xsm_risk_hours": 168, "xsm_rebalance_hours": 24, "split_gap_hours": 12},
        "capture_errors": errors, "coverage": coverage(bars, funding, specs), "pair_training": model,
        "confirmatory_status": "INSUFFICIENT_DATA",
        "ten_asset_executable_status": "INSUFFICIENT_DATA: valuation/depth/history length unverified",
        "blocked": [
            {"id": "LEAD-LAG", "status": "INSUFFICIENT_DATA", "reason": "no-synchronous-external-receive-history"},
            {"id": "EVENT", "status": "INSUFFICIENT_DATA", "reason": "no-first-publication-expectation-vintages"},
            {"id": "TREND-V2", "status": "INSUFFICIENT_DATA", "reason": "no-accessible-implementation-or-result-bundle"}]}
    write_json(output / "manifest.json", manifest)
    write_json(output / "diagnostics.json", diagnostics(bars, funding, model, symbols))
    rows, details = [], output / "details.jsonl.gz"
    with gzip.open(details, "wt", encoding="utf-8") as stream:
        for d in definitions:
            for split, (start, stop) in splits.items():
                if start >= stop:
                    continue
                # Pair residual fitting uses development; never backtest earlier than its training cutoff.
                orders = orders_for(d, bars, funding, model, start, stop)
                for scenario, costs in [("base", Costs()),
                                        ("stress", Costs(fee_bps=18, slippage_bps=4, spread_bps=18,
                                                         delay_hours=2))]:
                    result = replay({s: bars[s] for s in d["symbols"]}, funding, specs, orders, start, stop, costs)
                    summary = result["summary"]
                    net = summary["net_scenario_return"]
                    if d["kind"] == "pair_coint" and model["status"] != "EXPLORATORY_GATE_PASS":
                        status = "INSUFFICIENT_DATA" if model["status"] == "INSUFFICIENT_DATA" else "REJECTED"
                        reason = model.get("reason", "training-stationarity-gate-failed-no-trade")
                    elif (summary["entry_signals"] == 0 or summary["fill_count"] == 0) and d["kind"] != "none":
                        status, reason = "INSUFFICIENT_DATA", "no-eligible-signals-in-window"
                    elif not summary["scenario_complete"]:
                        status, reason = "INSUFFICIENT_DATA", "unknown-funding-or-stale-mark-or-unclosed-position"
                    elif net is not None and net < 0:
                        status, reason = "REJECTED", "negative-cost-scenario-return"
                    else:
                        status, reason = "INCONCLUSIVE", "positive-or-flat-seen-scenario-not-independent-evidence"
                    row = {"strategy_id": d["id"], "family": d["family"], "assets": ";".join(d["symbols"]),
                           "timeframe": "1h", "split": split, "scenario": scenario,
                           "start_ms": start, "end_ms": stop, "hours": (stop - start) // HOUR,
                           "status": status, "failure_reason": reason, **summary}
                    rows.append(row)
                    stream.write(json.dumps({"strategy_id": d["id"], "split": split, "scenario": scenario,
                                             **result}, allow_nan=False) + "\n")
    write_json(output / "summary.json", rows)
    columns = ["strategy_id", "family", "assets", "timeframe", "split", "scenario", "hours",
               "signals", "entry_signals", "fill_count", "closed_segments", "trade_batches", "gross_return",
               "net_return", "net_scenario_return", "known_components_return", "fees", "spread_slippage_cost", "funding_scenario",
               "win_rate_scenario", "profit_factor_scenario", "max_drawdown_scenario", "daily_sharpe_descriptive",
               "mean_gross_fraction", "max_gross_fraction", "turnover_notional", "funding_unknown_position_hours",
               "stale_position_marks", "rejected_orders", "status", "failure_reason"]
    with (output / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    # Human report is derived from the frozen output, never manual PnL editing.
    text = ["# 实际传统策略实验：Retrospective / Seen", "", "自动生成；所有收益均为 OHLC 线性估值和成本情景。",
            "历史可执行净收益 net_return 全部为 unknown；不是确认性测试。", "",
            "| 策略 | 资产 | 信号/成交段 | 毛回报% | 净情景% | 费用/摩擦 | 回撤% | 状态 |",
            "|---|---|---:|---:|---:|---:|---:|---|"]
    def pct(value):
        return "unknown" if value is None else f"{value * 100:.3f}"
    for row in rows:
        if row["split"] == "full_seen" and row["scenario"] == "base":
            text.append(f"| {row['strategy_id']} | {row['assets']} | {row['entry_signals']}/{row['closed_segments']} | "
                        f"{pct(row['gross_return'])} | {pct(row['net_scenario_return'])} | "
                        f"{row['fees']:.2f}/{row['spread_slippage_cost']:.2f} | "
                        f"{pct(row['max_drawdown_scenario'])} | {row['status']} |")
    text += ["", "完整 split、压力情景、胜率、Profit Factor、风险与换手见 comparison.csv。",
             "逐次信号、成交、结算、净值与拒绝原因见 details.jsonl.gz。",
             "协整检验、数据覆盖、时间边界、依赖与源码摘要见 manifest.json。", ""]
    (output / "results.zh-CN.md").write_text("\n".join(text), encoding="utf-8")
    write_json(output / "artifact-hashes.json", {p.name: source_hash(p) for p in sorted(output.iterdir())
                                                if p.is_file()})
    print(json.dumps({"experiments": len(rows), "output": str(output), "pair_gate": model["status"],
                      "first_utc": datetime.fromtimestamp(first / 1000, timezone.utc).isoformat(),
                      "last_utc": datetime.fromtimestamp(end / 1000, timezone.utc).isoformat()}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.archive, args.output)


if __name__ == "__main__":
    main()
