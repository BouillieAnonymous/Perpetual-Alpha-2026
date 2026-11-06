"""Offline contract checks for the frozen PAS research archive and causal replay."""

import gzip
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pas_momentum_core import features, simulate  # noqa: E402
from pas_research_costs import BasisCostModel  # noqa: E402


class ResearchRecordTests(unittest.TestCase):
    def quotes(self, count=160):
        return [
            dict(
                symbol="pas:ETH",
                t=(30000000 + i) * 60000 + 100,
                event=(30000000 + i) * 60000,
                bid=100 + 0.05 * i + 0.02 * (i % 3),
                ask=100.02 + 0.05 * i + 0.02 * (i % 3),
                sizes=[100, 100],
                funding=0.00001,
                decimals=4,
                oracle=100,
            )
            for i in range(count)
        ]

    def test_features_do_not_change_when_future_quotes_arrive(self):
        quotes = self.quotes()
        full, _ = features(quotes)
        prefix, _ = features(quotes[:80])
        self.assertTrue(prefix)
        self.assertEqual(prefix, [p for p in full if p["t"] <= quotes[79]["t"]])
        for p in full:
            self.assertLessEqual(p["q"]["t"], p["t"])

    def test_flat_prices_cannot_manufacture_standardized_momentum(self):
        quotes = self.quotes()
        for q in quotes:
            q.update(bid=100, ask=100.02)
        points, rejected = features(quotes)
        self.assertFalse(points)
        self.assertGreater(rejected["zero_volatility"], 0)

    def test_depth_rejection_and_cost_filter_prevent_entries(self):
        quotes = self.quotes()
        points, _ = features(quotes)
        model = dict(beta=0, intercept=0)
        filtered = simulate(points, quotes, model, 5, quotes[-1]["t"], "momentum", filtered=True)
        self.assertEqual(filtered["n"], 0)
        self.assertGreater(filtered["counters"]["expected_cost_block"], 0)
        for q in quotes:
            q["sizes"] = [0, 0]
        blocked = simulate(points, quotes, model, 5, quotes[-1]["t"], "momentum")
        self.assertEqual(blocked["n"], 0)
        self.assertGreater(blocked["counters"]["entry_depth_or_minimum"], 0)

    def test_next_quote_execution_and_positions_do_not_overlap(self):
        quotes = self.quotes()
        points, _ = features(quotes)
        result = simulate(points, quotes, dict(beta=0, intercept=0), 5, quotes[-1]["t"], "momentum")
        self.assertGreater(result["n"], 1)
        previous_exit = -1
        for trade in result["trades"]:
            self.assertGreater(trade["decision_ms"], previous_exit)
            self.assertGreaterEqual(trade["entry_ms"] - trade["decision_ms"], 1000)
            self.assertGreaterEqual(trade["exit_ms"] - trade["entry_ms"], 5 * 60000)
            previous_exit = trade["exit_ms"]

    def test_cost_filter_strictly_requires_surplus(self):
        cost = BasisCostModel()
        self.assertFalse(cost.estimate("up", "100", "100.01", "100.02", "0", 300000)["eligible"])
        self.assertTrue(cost.estimate("up", "100", "100.01", "102", "0", 300000)["eligible"])

    def test_all_asset_frozen_trade_ledgers_balance(self):
        path = ROOT / "docs/research/all-asset-momentum-2026-10-20/results.json.gz"
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            record = json.load(stream)
        self.assertEqual(len(record["assets"]), 10)
        checked = 0
        for asset in record["assets"].values():
            for horizon in asset["horizons"].values():
                for name, result in horizon["simulations"].items():
                    rate = Decimal(
                        18 if name.endswith("raw-double") else 0 if name.endswith("raw-zero") else 9
                    )
                    total = Decimal(0)
                    for trade in result["trades"]:
                        qty, entry, exit_price = (Decimal(str(trade[k])) for k in ("qty", "entry", "exit"))
                        net = (
                            Decimal(trade["side"]) * qty * (exit_price - entry)
                            - qty * (entry + exit_price) * rate / 10000
                            - Decimal(str(trade["funding_stress"]))
                        )
                        self.assertLess(abs(net - Decimal(str(trade["net"]))), Decimal("1e-8"))
                        total += net
                        checked += 1
                    self.assertLess(abs(total - Decimal(str(result["net"]))), Decimal("1e-7"))
                    self.assertEqual(result["counters"].get("unresolved_exit", 0), 0)
        self.assertEqual(checked, 9234)


if __name__ == "__main__":
    unittest.main()
