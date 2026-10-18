"""Descriptive paired comparisons/concentration; do not fit new rules from results."""
import gzip
import json
import statistics
from pathlib import Path

from perpetual_alpha.offline_quant.data import HOUR


def analyze(root):
    details = {}
    with gzip.open(root / "experiments/details.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["split"] == "full_seen" and row["scenario"] == "base":
                details[row["strategy_id"]] = row
    daily, concentration = {}, {}
    for name, detail in details.items():
        end_values = {}
        for row in detail["equity"]:
            end_values[(row["time_ms"] - 1) // (24 * HOUR)] = row["equity_scenario"]
        previous, pnl = 10000, {}
        for day, value in end_values.items():
            pnl[day] = value - previous
            previous = value
        daily[name] = pnl
        positives = [max(0, x) for x in pnl.values()]
        trade_positives = sorted((max(0, t["net_scenario"]) for t in detail["trades"]), reverse=True)
        concentration[name] = {"daily_pnl_scenario": pnl,
            "largest_day_positive_contribution_share": max(positives) / sum(positives) if sum(positives) else None,
            "top3_closed_segments_positive_share": sum(trade_positives[:3]) / sum(trade_positives)
            if sum(trade_positives) else None,
            "warning": "closed segments are correlated and may include partial reductions; not independent trades"}
    paired = []
    for candidate, parent in [
        ("DONCHIAN_COMPRESSION", "DONCHIAN"), ("DONCHIAN_EXPANSION", "DONCHIAN"),
        ("DONCHIAN_COMPRESSION_EXPANSION", "DONCHIAN"), ("REGIME-TREND", "DONCHIAN"),
        ("REGIME-RANGE", "DONCHIAN"), ("REGIME-COST", "DONCHIAN"), ("REGIME-RANDOM", "DONCHIAN"),
        ("XSM-TEN-PRICE-SCENARIO-RISK", "XSM-TEN-PRICE-SCENARIO-TSM"),
        ("XSM-BTC-ETH-SUBSET-RISK", "XSM-BTC-ETH-SUBSET-TSM")]:
        differences = [daily[candidate][d] - daily[parent][d] for d in sorted(set(daily[candidate]) & set(daily[parent]))]
        paired.append({"candidate": candidate, "parent": parent, "days": len(differences),
                       "total_pnl_difference_scenario": sum(differences),
                       "mean_day_pnl_difference_scenario": statistics.mean(differences),
                       "positive_difference_days": sum(x > 0 for x in differences),
                       "block_confidence_interval": None,
                       "inference_status": "INSUFFICIENT_DATA: fewer than 8 independent 3-day blocks",
                       "candidate_mean_gross": details[candidate]["summary"]["mean_gross_fraction"],
                       "parent_mean_gross": details[parent]["summary"]["mean_gross_fraction"]})
    output = {"schema": "descriptive-comparisons/1", "paired_daily": paired, "concentration": concentration,
              "sensitivity_timing": "XSM 12/48h and funding 24h added after initial results as diagnostic only; no winner selected"}
    (root / "comparison-analysis.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print("paired daily comparisons and concentration saved; no significance or Alpha claim")


if __name__ == "__main__":
    analyze(Path("docs/research/new-alpha-discovery-2026-10-18"))
