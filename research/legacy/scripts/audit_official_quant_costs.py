"""Independent Decimal re-accounting of FROZEN fills using the published fee formula.

This does NOT assume maker fills, invent historical fees, or change signals/quantities.
Unknown settlement oracle and execution make executable net returns unknown throughout.
"""
import argparse
import csv
import gzip
import hashlib
import json
from decimal import Decimal
from pathlib import Path

GUIDE = "https://perpetual-alpha26.github.io/docs/"
FEES = "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees#fee-formula-for-developers"
FUNDING = "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding"


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Finite decimal required")
    return result


def fee_rates(maker_base="0.00015", taker_base="0.00045", *, deployer_scale="1",
              growth_mode=False, referral_discount="0", aligned_quote=False):
    """Return fractional rates, NOT percentages. Inputs represent a documented scenario."""
    maker, taker, scale, referral = map(decimal, (maker_base, taker_base, deployer_scale, referral_discount))
    if (not 0 <= scale <= 10 or not 0 <= referral < 1 or not -1 < maker < 1 or not 0 <= taker < 1
            or type(growth_mode) is not bool or type(aligned_quote) is not bool):
        raise ValueError("Invalid fee formula inputs")
    hip3 = scale + 1 if scale < 1 else scale * 2
    share = scale / (1 + scale) if scale < 1 else Decimal(".5")
    growth = Decimal(".1") if growth_mode else Decimal(1)
    maker *= growth
    if maker > 0:
        maker *= hip3 * (1 - referral)
    elif aligned_quote:
        maker *= (1 - share) * Decimal("1.5") + share
    taker *= hip3 * growth * (1 - referral)
    if aligned_quote:
        taker *= (1 - share) * Decimal(".8") + share
    return maker, taker


def funding_payment(signed_quantity, oracle, hourly_rate):
    """Trader cashflow: positive funding means a long pays. Rate already HOURLY."""
    quantity, oracle, rate = map(decimal, (signed_quantity, oracle, hourly_rate))
    if oracle <= 0 or not -1 < rate < 1:
        raise ValueError("Invalid funding payment inputs")
    return -quantity * oracle * rate


def account(detail):
    summary = detail["summary"]
    maker, taker = fee_rates()
    actual_notional = sum((abs(decimal(f["delta"]) * decimal(f["scenario_price"]))
                           for f in detail["fills"]), Decimal(0))
    reference_notional = sum((abs(decimal(f["delta"]) * decimal(f["reference"]))
                              for f in detail["fills"]), Decimal(0))
    fees = actual_notional * taker
    reference_fees = reference_notional * taker
    maker_sensitivity = actual_notional * maker
    friction = sum((decimal(f["friction"]) for f in detail["fills"]), Decimal(0))
    funding = sum((funding_payment(f["quantity"], f["price_proxy"], f["rate"])
                   for f in detail["funding"]), Decimal(0))
    if abs(funding - decimal(summary["funding_scenario"])) > Decimal("1e-7"):
        raise ValueError("Funding re-accounting mismatch")
    # Frozen STRESS fills were charged 18bps, so a 9bps re-account deliberately differs.
    declared_fee = decimal(summary["costs"]["fee_bps"]) / 10000
    if abs(actual_notional * declared_fee - decimal(summary["fees"])) > Decimal("1e-7"):
        raise ValueError("Original fee reconciliation mismatch")
    capital = decimal(summary["initial_capital"])
    gross = decimal(summary["gross_price_pnl"]) if summary["gross_price_pnl"] is not None else None
    complete = summary["scenario_complete"] and gross is not None
    before_fees = gross + funding if complete else None
    fee_only = before_fees - reference_fees if complete else None
    taker_net = before_fees - fees - friction if complete else None
    maker_net = before_fees - maker_sensitivity - friction if complete else None
    base_match = (float(taker_net / capital) - summary["net_scenario_return"]
                  if complete and declared_fee == taker else None)
    if base_match is not None and abs(base_match) > 1e-10:
        raise ValueError("Base official fee re-accounting changed prior net PnL")
    def amount(x):
        return None if x is None else float(x)
    def ret(x):
        return None if x is None else float(x / capital)
    return {"strategy_id": detail["strategy_id"], "split": detail["split"],
        "frozen_execution_scenario": detail["scenario"],
        "gross_return": ret(gross), "funding_close_proxy": amount(funding),
        "fees_original": summary["fees"], "fees_official_base_taker": amount(fees),
        "fees_official_base_maker_rate_sensitivity": amount(maker_sensitivity),
        "frozen_execution_friction": amount(friction),
        "fee_only_zero_friction_return": ret(fee_only),
        "official_taker_with_frozen_friction_return": ret(taker_net),
        "maker_rate_sensitivity_with_frozen_prices_return": ret(maker_net),
        "official_taker_price_only_after_fee_return": ret(gross - reference_fees) if complete else None,
        "break_even_total_cost_per_traded_notional_bps": float(before_fees / reference_notional * 10000)
        if complete and reference_notional else None,
        "remaining_friction_budget_after_9bps_per_fill": float(fee_only / reference_notional * 10000)
        if complete and reference_notional else None,
        "turnover_reference_notional": amount(reference_notional),
        "base_reconciliation_return_difference": base_match,
        "official_fee_alone_erases_positive_gross": bool(complete and gross > 0 and gross - reference_fees <= 0),
        "positive_after_fee_and_proxy_funding": bool(complete and fee_only > 0),
        "positive_with_frozen_friction": bool(complete and taker_net > 0),
        "fill_count": len(detail["fills"]), "closed_segments": summary["closed_segments"],
        "executable_net_return": None, "official_exact_funding": None,
        "scenario_complete": complete,
        "limitation": "base tier, historical user discount unknown; hypothetical fills; settlement oracle replaced by prior close"}


def audit(root):
    output = root / "official-cost-audit"
    if output.exists():
        raise FileExistsError("Keep previous audit; use a new research root/output")
    input_path = root / "experiments/details.jsonl.gz"
    rows = []
    with gzip.open(input_path, "rt", encoding="utf-8") as stream:
        rows = [account(json.loads(line)) for line in stream]
    output.mkdir()
    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                                  encoding="utf-8", newline="\n")
    save("summary.json", rows)
    inputs = {"sources": {"guide": GUIDE, "fees": FEES, "funding": FUNDING}, "retrieval_date": "2026-10-18",
        "formula_inputs": {"user_maker_base_fraction": ".00015", "user_taker_base_fraction": ".00045",
                           "deployer_scale": 1, "growth_mode": False, "referral_discount": 0,
                           "aligned_quote_discount": False},
        "effective_fractional_rates": {"maker": str(fee_rates()[0]), "taker": str(fee_rates()[1])},
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "audit_script_sha256": hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode()).hexdigest(),
        "scope": "352 frozen experiments: all splits and both frozen execution schedules",
        "fee_only": "optimistic zero-spread/slippage reference-price sensitivity; NOT an executable backtest",
        "maker": "fee sensitivity on IDENTICAL hypothetical fills; NOT proof of maker fill probability",
        "funding": "official signed payment formula, archived hourly rates, prior-close oracle PROXY",
        "actual_historical_userFees": "UNKNOWN; no account requests made",
        "official_exact_total_cost_and_executable_return": "UNKNOWN"}
    save("manifest.json", inputs)
    with (output / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    base = [r for r in rows if r["split"] == "full_seen" and r["frozen_execution_scenario"] == "base"]
    stats = {"experiments": len(rows), "full_seen_base_versions": len(base),
             "positive_gross_versions": sum(r["gross_return"] is not None and r["gross_return"] > 0 for r in base),
             "positive_fee_only_versions": sum(r["positive_after_fee_and_proxy_funding"] for r in base),
             "positive_frozen_friction_versions": sum(r["positive_with_frozen_friction"] for r in base),
             "positive_gross_erased_by_fee_alone": sum(r["official_fee_alone_erases_positive_gross"] for r in base),
             "warning": "versions overlap and include aliases/control/sensitivity; not independent strategies"}
    save("counts.json", stats)
    save("hashes.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()
                         if p.is_file()})
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("docs/research/new-alpha-discovery-2026-10-18"))
    audit(parser.parse_args().root)
