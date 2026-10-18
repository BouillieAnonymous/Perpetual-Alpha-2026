"""Economically material ledger and anti-lookahead checks, fully offline."""
import gzip
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from perpetual_alpha.offline_quant.data import HOUR, Bar, read_archive
from perpetual_alpha.offline_quant.relative import fit_pair, funding_orders, pair_orders
from perpetual_alpha.offline_quant.replay import Costs, quantity, replay
from perpetual_alpha.offline_quant.signals import (
    Order,
    cross_section_orders,
    event_orders,
    lead_lag_feature,
    single_orders,
)


def bars(n=460, symbol="par:BTC", constant=False):
    rows = []
    previous = 100
    for i in range(n):
        price = 100 if constant else 100 * math.exp(.0004 * i + .02 * math.sin(i / 9))
        rows.append(Bar(symbol, (i + 1) * HOUR, previous, max(price, previous) + .01,
                        min(price, previous) - .01, price, 1, 999 * HOUR))
        previous = price
    return rows


def specs(symbols):
    return {s: {"quantity_step": ".0001", "valuation_supported": False} for s in symbols}


def funding(symbols, n=500, rate=0):
    return {s: {i * HOUR + 100: rate for i in range(n)} for s in symbols}


class SignalTests(unittest.TestCase):
    def test_future_mutation_cannot_change_past_signals_all_numeric_baselines(self):
        original = bars()
        mutated = [replace(r, close=r.close * 5, high=r.high * 5, low=r.low * 5) if i > 360 else r
                   for i, r in enumerate(original)]
        for kind in ["donchian", "atr", "donchian_compression", "donchian_expansion",
                     "donchian_compression_expansion", "tsm", "risk_momentum", "ema",
                     "rolling_z", "bollinger", "simple_mr"]:
            cutoff = original[359].time
            a = [o for o in single_orders(original, kind) if o.time <= cutoff]
            b = [o for o in single_orders(mutated, kind) if o.time <= cutoff]
            self.assertEqual(a, b, kind)

    def test_bollinger_and_same_z_score_are_equivalent_not_independent_alpha(self):
        self.assertEqual(single_orders(bars(), "rolling_z"), single_orders(bars(), "bollinger"))

    def test_channel_excludes_current_bar_and_flat_sigma_is_safe(self):
        rows = bars(constant=True)
        changed = rows[:40] + [replace(rows[40], close=110, high=111)]
        entries = [o for o in single_orders(changed, "donchian") if o.reason == "entry"]
        self.assertEqual(entries[0].time, rows[40].time)
        self.assertGreater(entries[0].targets["par:BTC"], 0)
        self.assertEqual(single_orders(rows, "donchian_compression_expansion"), [])

    def test_cross_section_uses_common_window_and_budget_and_past_only(self):
        symbols = ["par:BTC", "par:ETH"]
        data = {s: bars(symbol=s) for s in symbols}
        orders = cross_section_orders(data, symbols, "risk", count=1)
        self.assertTrue(orders)
        for order in orders:
            self.assertAlmostEqual(sum(order.targets.values()), 0)
            self.assertLessEqual(max(map(abs, order.targets.values())), .2 + 1e-12)
        cutoff = 300 * HOUR
        changed = {s: [replace(r, close=r.close * 100) if r.time > cutoff else r for r in rows]
                   for s, rows in data.items()}
        self.assertEqual([o for o in orders if o.time <= cutoff],
                         [o for o in cross_section_orders(changed, symbols, "risk", count=1)
                          if o.time <= cutoff])
        missing = {s: list(v) for s, v in data.items()}
        missing[symbols[0]] = [r for r in missing[symbols[0]] if r.time != 290 * HOUR]
        self.assertFalse(any(o.time == 312 * HOUR for o in cross_section_orders(missing, symbols, "risk", count=1)))

    def test_funding_signal_does_not_use_future_settlement(self):
        data = {"par:BTC": bars()}
        history = funding(data, rate=.0001)
        original = funding_orders(data, history, list(data))
        history["par:BTC"][400 * HOUR + 100] = -.1
        changed = funding_orders(data, history, list(data))
        self.assertEqual([o for o in original if o.time <= 400 * HOUR],
                         [o for o in changed if o.time <= 400 * HOUR])

    def test_asof_join_rejects_future_availability_and_stale_source(self):
        observations = [{"event_ms": 1, "received_ms": 2, "available_ms": 10, "price": 100},
                        {"event_ms": 3, "received_ms": 4, "available_ms": 30, "price": 200}]
        self.assertEqual(lead_lag_feature(observations, 20, max_age_ms=20)["price"], 100)
        self.assertIsNone(lead_lag_feature(observations, 5, max_age_ms=20))
        self.assertIsNone(lead_lag_feature(observations, 100, max_age_ms=20))
        self.assertIsNone(lead_lag_feature([{"event_ms": 1, "received_ms": 90, "available_ms": 90}],
                                          100, max_age_ms=20))

    def test_pair_model_fits_only_training_and_gate_blocks_trading(self):
        data = {"par:BTC": bars(), "par:ETH": [replace(r, close=r.close * (1 + .01 * math.sin(i / 7)))
                                                 for i, r in enumerate(bars(symbol="par:ETH"))]}
        cutoff = 200 * HOUR
        model = fit_pair(data, list(data), cutoff)
        changed = {s: [replace(r, close=r.close * 10) if r.time >= cutoff else r for r in rows]
                   for s, rows in data.items()}
        self.assertEqual(model, fit_pair(changed, list(data), cutoff))
        self.assertLess(model["fit_last_ms"], cutoff)
        self.assertEqual(pair_orders(data, list(data), {**model, "status": "REJECTED"}), [])

    def test_pair_model_insufficient_constant_or_degenerate_history(self):
        data = {s: bars(80, symbol=s, constant=True) for s in ["par:BTC", "par:ETH"]}
        self.assertEqual(fit_pair(data, list(data), 200 * HOUR)["status"], "INSUFFICIENT_DATA")
        data = {s: bars(200, symbol=s) for s in ["par:BTC", "par:ETH"]}
        self.assertEqual(fit_pair(data, list(data), 200 * HOUR)["reason"], "degenerate-residual")

    def test_event_rules_reject_retrospective_expectations_and_unannounced_calendar(self):
        rows = bars(3)
        event = {"known_ms": 100 * HOUR, "release_ms": HOUR, "received_ms": HOUR,
                 "expectation_available_ms": 2 * HOUR, "actual": 10, "expected": 5}
        surprise = event_orders(rows, [event], mode="surprise")
        self.assertTrue(all(not o.targets["par:BTC"] for o in surprise))
        event["known_ms"] = 0
        surprise = event_orders(rows, [event], mode="surprise")
        self.assertTrue(all(not o.targets["par:BTC"] for o in surprise))
        event["expectation_available_ms"] = 0
        self.assertGreater(event_orders(rows, [event], mode="surprise")[0].targets["par:BTC"], 0)
        paused = event_orders(rows, [event])
        self.assertEqual(paused[0].targets["par:BTC"], 0)


class LedgerTests(unittest.TestCase):
    def run_case(self, direction=1, rate=0, costs=None, history=None, orders=None):
        series = {"par:BTC": bars(8, constant=True)}
        orders = orders or [Order(HOUR, {"par:BTC": direction * .1}, "enter"),
                            Order(4 * HOUR, {"par:BTC": 0}, "exit")]
        return replay(series, funding(series, rate=rate) if history is None else history,
                      specs(series), orders, HOUR, 8 * HOUR, costs or Costs())

    def test_flat_roundtrip_costs_exact_funding_and_no_fabricated_executable_return(self):
        result = self.run_case()
        s = result["summary"]
        self.assertEqual(s["gross_price_pnl"], 0)
        self.assertAlmostEqual(s["fees"], 1.8)
        self.assertAlmostEqual(s["spread_slippage_cost"], 1)
        self.assertAlmostEqual(s["net_scenario_return"], -.00028)
        self.assertIsNone(s["net_return"])
        self.assertEqual(result["fills"][0]["time_ms"], 2 * HOUR)

    def test_funding_sign_long_pays_short_receives_and_actual_settlement_count(self):
        long, short = self.run_case(rate=.001), self.run_case(direction=-1, rate=.001)
        self.assertAlmostEqual(long["summary"]["funding_scenario"], -3)
        self.assertAlmostEqual(short["summary"]["funding_scenario"], 3)
        self.assertEqual(len(long["funding"]), 3)
        self.assertTrue(all(r["price_proxy_time_ms"] <= r["time_ms"] for r in long["funding"]))

    def test_missing_funding_is_unknown_not_zero(self):
        result = self.run_case(history={})["summary"]
        self.assertIsNone(result["net_scenario_return"])
        self.assertGreater(result["funding_unknown_position_hours"], 0)

    def test_exact_hour_settlement_boundary_is_not_misclassified_as_missing(self):
        history = {"par:BTC": {i * HOUR: .001 for i in range(20)}}
        result = self.run_case(history=history)
        self.assertEqual(result["summary"]["funding_unknown_position_hours"], 0)
        self.assertAlmostEqual(result["summary"]["funding_scenario"], -3)
        self.assertEqual([r["time_ms"] for r in result["funding"]], [3 * HOUR, 4 * HOUR, 5 * HOUR])

    def test_reversal_fees_and_closed_ledger_identity(self):
        orders = [Order(HOUR, {"par:BTC": .1}, "enter"),
                  Order(3 * HOUR, {"par:BTC": -.1}, "reverse"),
                  Order(5 * HOUR, {"par:BTC": 0}, "exit")]
        result = self.run_case(orders=orders)
        self.assertEqual(result["summary"]["closed_segments"], 2)
        self.assertAlmostEqual(sum(t["net_scenario"] for t in result["trades"]),
                               result["summary"]["net_scenario_return"] * 10000)
        self.assertAlmostEqual(result["summary"]["fees"], 3.6)

    def test_terminal_liquidation_costed_not_discarded(self):
        result = self.run_case(orders=[Order(HOUR, {"par:BTC": .1}, "entry")])
        self.assertEqual(result["fills"][-1]["reason"], "terminal-close-scenario")
        self.assertEqual(result["summary"]["unclosed_positions"], {})
        self.assertLess(result["summary"]["net_scenario_return"], 0)

    def test_missing_terminal_quote_retains_position_and_net_unknown(self):
        data = {"par:BTC": bars(8, constant=True)[:-1]}
        result = replay(data, funding(data), specs(data), [Order(HOUR, {"par:BTC": .1}, "entry")],
                        HOUR, 8 * HOUR)
        self.assertTrue(result["summary"]["unclosed_positions"])
        self.assertIsNone(result["summary"]["net_scenario_return"])

    def test_higher_friction_same_prices_and_schedule_cannot_improve_flat_roundtrip(self):
        base = self.run_case()["summary"]
        stress = self.run_case(costs=Costs(fee_bps=18, spread_bps=18, slippage_bps=4))["summary"]
        self.assertLess(stress["net_scenario_return"], base["net_scenario_return"])

    def test_missing_leg_rejects_whole_target_batch(self):
        data = {"par:BTC": bars(8), "par:ETH": bars(8, symbol="par:ETH")}
        data["par:ETH"] = [r for r in data["par:ETH"] if r.time != 3 * HOUR]
        result = replay(data, funding(data), specs(data), [Order(HOUR, {"par:BTC": .1, "par:ETH": -.1}, "pair")],
                        HOUR, 8 * HOUR)
        self.assertEqual(result["fills"], [])
        self.assertEqual(result["rejections"][0]["reason"], "missing-basket-open-or-spec")

    def test_risk_limits_and_invalid_friction_fail_closed(self):
        with self.assertRaises(ValueError):
            self.run_case(orders=[Order(HOUR, {"par:BTC": .21}, "over-budget")])
        with self.assertRaises(ValueError):
            Costs(fee_bps=float("nan"))
        with self.assertRaises(ValueError):
            Costs(delay_hours=0)
        self.assertEqual(quantity(100, 30, ".1"), 3.3)
        self.assertEqual(quantity(-100, 30, ".1"), -3.3)

    def test_corrupt_archive_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "bad.gz"
            with gzip.open(p, "wt", encoding="utf-8") as stream:
                stream.write(json.dumps({"request": {"type": "meta"}, "payload": {},
                                         "received_ms": 1, "payload_sha256": "wrong"}) + "\n")
            with self.assertRaises(ValueError):
                read_archive(p)


if __name__ == "__main__":
    unittest.main()
