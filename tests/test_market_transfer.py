"""Transfer anti-lookahead, quote-side, depth, shared-sizing and interval checks."""
import gzip
import hashlib
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from perpetual_alpha.market_transfer import (
    Book,
    asof_book,
    association,
    load_books,
    quote_exit,
    quote_price,
    received_quote_trial,
    transfer_orders,
)
from perpetual_alpha.offline_quant.data import HOUR, Bar
from perpetual_alpha.offline_quant.signals import single_orders


def book(time, *, venue="par", price=100., size=10., session="s", event=None):
    return Book(venue, "par:BTC", time if event is None else event, time, time, session,
                ((price - .1, size),), ((price + .1, size),))


def series(n=120, step=HOUR):
    rows = []
    for i in range(n):
        p = 100 * math.exp(.002 * i + .03 * math.sin(i / 8))
        rows.append(Bar("par:BTC", (i + 1) * step, p, p + .1, p - .1, p, 1, 1000 * HOUR))
    return rows


class TransferTests(unittest.TestCase):
    def test_future_available_external_book_cannot_be_used(self):
        history = [book(100, venue="external"), replace(book(200, venue="external"), available_ms=500)]
        self.assertEqual(asof_book(history, 300, venue="external", symbol="par:BTC", session="s").received_ms, 100)

    def test_old_event_new_receipt_rejected_and_no_clock_shift(self):
        history = [book(20000, venue="external", event=0)]
        self.assertIsNone(asof_book(history, 20000, venue="external", symbol="par:BTC", session="s"))
        history = [book(100, venue="external", event=101)]
        self.assertIsNone(asof_book(history, 100, venue="external", symbol="par:BTC", session="s"))

    def test_receipt_proxy_only_does_not_claim_decoded_availability(self):
        history = [replace(book(100, venue="external"), available_kind="receive-only-proxy")]
        self.assertIsNone(asof_book(history, 100, venue="external", symbol="par:BTC", session="s"))

    def test_session_boundaries_prevent_cross_capture_join(self):
        history = [book(100, venue="external", session="old")]
        self.assertIsNone(asof_book(history, 101, venue="external", symbol="par:BTC", session="new"))

    def test_exit_is_next_available_not_nearest_or_stale(self):
        history = [book(99), book(102)]
        self.assertEqual(quote_exit(history, 100, symbol="par:BTC", session="s").available_ms, 102)
        self.assertIsNone(quote_exit(history, 100, symbol="par:BTC", session="s", max_late_ms=1))
        self.assertIsNone(quote_exit([book(20000, event=0)], 19000, symbol="par:BTC", session="s"))

    def test_buy_ask_sell_bid_and_top_level_depth(self):
        q = book(100, size=1)
        self.assertEqual(quote_price(q, 1, 1), 100.1)
        self.assertEqual(quote_price(q, -1, 1), 99.9)
        self.assertIsNone(quote_price(q, 1, 1.01))
        with self.assertRaises(ValueError):
            quote_price(q, 1, 0)

    def test_missing_four_hour_exit_is_not_removed_or_zero(self):
        history = [book(0, venue="external", price=90), book(100, venue="external", price=100),
                   book(101), book(102)]
        result = received_quote_trial(history, 100, symbol="par:BTC", session="s", source="external",
                                      lookback_ms=100, hold_ms=4 * HOUR, latency_ms=1)
        self.assertEqual(result["status"], "UNFINISHED_EXIT")
        self.assertIsNone(result["net_pnl"])

    def test_flat_quote_roundtrip_costs_money_even_with_external_direction(self):
        history = [book(0, venue="external", price=90), book(100, venue="external", price=100),
                   book(101), book(201)]
        result = received_quote_trial(history, 100, symbol="par:BTC", session="s", source="external",
                                      lookback_ms=100, hold_ms=100, latency_ms=1)
        self.assertEqual(result["status"], "QUOTE_SCENARIO_ONLY_FUNDING_UNKNOWN")
        self.assertLess(result["scenario_price_pnl_after_fee_before_funding"], 0)
        self.assertIsNone(result["net_pnl"])

    def test_reversed_source_signal_uses_own_quote_sides_not_negated_net(self):
        common = [book(101), book(201)]
        long = received_quote_trial([book(0, venue="external", price=90),
                book(100, venue="external", price=100)] + common, 100,
                symbol="par:BTC", session="s", source="external", lookback_ms=100, hold_ms=100, latency_ms=1)
        short = received_quote_trial([book(0, venue="external", price=110),
                book(100, venue="external", price=100)] + common, 100,
                symbol="par:BTC", session="s", source="external", lookback_ms=100, hold_ms=100, latency_ms=1)
        self.assertLess(long["scenario_price_pnl_after_fee_before_funding"], 0)
        self.assertLess(short["scenario_price_pnl_after_fee_before_funding"], 0)
        self.assertEqual(long["quantity"], short["quantity"])

    def test_future_price_changes_do_not_change_past_transferred_signals(self):
        rows = series()
        altered = [replace(r, close=r.close * 10, high=r.high * 10, low=r.low * 10) if i > 80 else r
                   for i, r in enumerate(rows)]
        for strategy in ("tsm", "ema", "donchian"):
            a = transfer_orders(rows, strategy, 26 * HOUR, 120 * HOUR)
            b = transfer_orders(altered, strategy, 26 * HOUR, 120 * HOUR)
            self.assertEqual([o for o in a if o.time <= 80 * HOUR], [o for o in b if o.time <= 80 * HOUR])

    def test_same_fixed_par_budget_for_any_information_source(self):
        rows = series()
        scaled = [replace(r, close=r.close * 10, open=r.open * 10, high=r.high * 10, low=r.low * 10) for r in rows]
        a = transfer_orders(rows, "tsm", 26 * HOUR, 120 * HOUR)
        b = transfer_orders(scaled, "tsm", 26 * HOUR, 120 * HOUR)
        self.assertEqual([(o.time, o.targets) for o in a], [(o.time, o.targets) for o in b])
        self.assertTrue(all(abs(v) in (0, .1) for o in a for v in o.targets.values()))

    def test_minute_and_five_minute_returns_not_price_level_only(self):
        rows = series(120, step=60000)
        result = association(rows, rows, base_interval="1m", return_minutes=(1, 5, 60))
        for item in result["return_association"]:
            if item["pearson"] is not None:
                self.assertAlmostEqual(item["pearson"], 1)
                self.assertAlmostEqual(item["tracking_error_bps"], 0)
        self.assertGreater(result["return_association"][0]["nonoverlapping_returns"],
                           result["return_association"][1]["nonoverlapping_returns"])

    def test_missing_interior_bar_not_bridged_by_endpoints(self):
        rows = series(120, step=60000)
        missing = [r for r in rows if r.time != 8 * 60000]
        full = association(rows, rows, base_interval="1m", return_minutes=(5,))
        partial = association(rows, missing, base_interval="1m", return_minutes=(5,))
        self.assertEqual(full["return_association"][0]["nonoverlapping_returns"] - 1,
                         partial["return_association"][0]["nonoverlapping_returns"])

    def test_external_book_cannot_be_labeled_as_par_execution(self):
        payload = {"coin": "BTC", "time": 1, "levels": [[{"px": "99", "sz": "1"}],
                                                               [{"px": "101", "sz": "1"}]]}
        row = {"venue": "par", "request": {"coin": "BTC"}, "payload": payload,
               "payload_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                stream.write(json.dumps(row) + "\n")
            with self.assertRaises(ValueError):
                load_books(path)

    def test_bounded_warmup_preserves_original_frozen_rule_directions(self):
        rows = series(400)
        for kind in ("tsm", "ema", "donchian"):
            full = single_orders(rows, kind, hold=4, active_start=300 * HOUR)
            trimmed = transfer_orders(rows, kind, 300 * HOUR, 401 * HOUR)
            self.assertEqual([(o.time, {s: (v > 0) - (v < 0) for s, v in o.targets.items()}) for o in full],
                             [(o.time, {s: (v > 0) - (v < 0) for s, v in o.targets.items()}) for o in trimmed])


if __name__ == "__main__":
    unittest.main()
