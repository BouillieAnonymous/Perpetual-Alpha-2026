import hashlib
import json
import unittest
from dataclasses import replace

from test_basis_convergence import CONFIG, quote

from perpetual_alpha.basis_convergence import Quote
from perpetual_alpha.basis_convergence import run as episodes
from perpetual_alpha.basis_direction import (
    correlation,
    funding_cash,
    official_funding,
    official_trade_check,
    ranks,
    run,
)


def study(rows, config=CONFIG):
    return run(rows, episodes(rows, config)["events"], config)


def observation(result, clock="next_quote", horizon=1):
    return next(r for r in result["observations"] if r["clock"] == clock and r["horizon_s"] == horizon)


def fixture():
    return [quote(1000), quote(2000, 99), quote(3000, 99), quote(4000, 99.5),
            quote(5000, 99.8), quote(6000), quote(7000)]


def official_record():
    payload = [dict(coin="par:BTC", time=t, fundingRate="0.0001") for t in (30, 3600030, 7200030)]
    return dict(request=dict(type="fundingHistory", coin="par:BTC", startTime=0, endTime=7200030),
                received_ms=8000000, error=None, payload=payload,
                payload_sha256=hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest())


class DirectionTests(unittest.TestCase):
    def test_long_cheap_and_short_expensive_predict_opposite_price_moves(self):
        long = observation(study(fixture()))
        short = observation(study([quote(1000), quote(2000, 101), quote(3000, 101),
                                   quote(4000, 100.5), quote(5000), quote(6000)]))
        self.assertGreater(long["mid_return_bps"], 0)
        self.assertLess(short["mid_return_bps"], 0)
        self.assertGreater(long["aligned_mid_bps"], 0)
        self.assertGreater(short["aligned_mid_bps"], 0)

    def test_next_quote_and_signal_have_separate_forward_clocks(self):
        result = study(fixture())
        signal, delayed = observation(result, "signal"), observation(result)
        self.assertEqual(signal["origin_ms"], 2000)
        self.assertEqual(signal["exit_ms"], 3000)
        self.assertEqual(delayed["origin_ms"], 3000)
        self.assertEqual(delayed["exit_ms"], 4000)

    def test_losing_net_is_not_negated_to_construct_reverse(self):
        row = observation(study(fixture()))
        self.assertLess(row["original_net_bps"] + row["opposite_net_bps"], 0)
        self.assertNotAlmostEqual(row["opposite_net_bps"], -row["original_net_bps"])

    def test_flat_prices_lose_in_both_directions(self):
        row = observation(study([quote(1000), quote(2000, 99), quote(3000, 99), quote(4000, 99)]))
        self.assertLess(row["original_net_bps"], 0)
        self.assertLess(row["opposite_net_bps"], 0)
        self.assertEqual(row["original_mfe_bps"], 0)
        self.assertLess(row["original_best_net_bps"], 0)

    def test_unconverged_events_are_not_discarded(self):
        rows = [quote(1000), quote(2000, 99), quote(3000, 99), quote(4000, 99.5)]
        self.assertIsNone(episodes(rows, CONFIG)["events"][0]["convergence_ms"])
        self.assertTrue(study(rows)["observations"])

    def test_direction_withdrawal_is_recorded_not_conditioned_on_future(self):
        rows = [quote(1000), quote(2000, 99), quote(3000), quote(4000, 100.1)]
        row = observation(study(rows))
        self.assertFalse(row["retained"])
        self.assertGreater(row["pre_entry_aligned_bps"], 0)

    def test_pre_and_post_entry_move_share_same_endpoint(self):
        rows = [quote(1000), quote(2000, 99), quote(3000, 99.5), quote(4000, 99.7), quote(5000, 99.8)]
        row = observation(study(rows), "signal", 3)
        self.assertAlmostEqual(row["aligned_mid_bps"], row["pre_entry_aligned_bps"] + row["post_entry_same_endpoint_bps"])

    def test_paired_sample_requires_reverse_depth_too(self):
        rows = fixture()
        rows[2]["book"]["levels"][0][0]["sz"] = "0.00001"
        result = study(rows)
        row = observation(result)
        self.assertTrue(row["long_eligible"])
        self.assertFalse(row["short_eligible"])
        def get(cohort):
            return next(g["result"] for g in result["summary"] if g["horizon_s"] == 1
                        and g["symbol"] == g["side"] == "ALL" and g["cohort"] == cohort)
        self.assertEqual(get("retained_executable")["n"], 1)
        self.assertEqual(get("paired_executable")["n"], 0)

    def test_same_quantity_is_capped_for_both_directions(self):
        row = observation(study(fixture()))
        self.assertLessEqual(row["quantity"] * 99.001 * 1.0002, 100)
        self.assertAlmostEqual(row["quantity"] / .0001, round(row["quantity"] / .0001), places=6)

    def test_mae_mfe_include_initial_cost_and_later_gain(self):
        row = observation(study(fixture()), horizon=3)
        self.assertLess(row["original_mae_bps"], -18)
        self.assertGreater(row["original_mfe_bps"], 0)
        self.assertLessEqual(row["opposite_mfe_bps"], row["original_mfe_bps"])

    def test_missing_official_funding_stays_unknown(self):
        self.assertIsNone(observation(study(fixture()))["official_original_net_bps"])

    def test_gap_does_not_invent_forward_return(self):
        result = study([quote(1000), quote(2000, 99), quote(3000, 99), quote(9000, 100)])
        self.assertFalse(any(r["clock"] == "next_quote" for r in result["observations"]))
        self.assertGreater(result["missing_observations"]["next_quote:horizon_unavailable"], 0)

    def test_bootstrap_is_reproducible_and_single_day_not_overconfident(self):
        config = replace(CONFIG, bootstrap_repeats=10)
        a, b = study(fixture(), config), study(fixture(), config)
        self.assertEqual(a, b)
        # CONFIG trigger=20; one UTC day cannot yield a multi-day interval.
        group = next(g for g in a["summary"] if g["cohort"] == "signal_all" and g["symbol"] == g["side"] == "ALL")
        self.assertTrue(all(v is None for v in group["result"]["day_bootstrap_95"].values()))


class FundingAndStatisticsTests(unittest.TestCase):
    def test_correlation_and_tie_ranks(self):
        self.assertEqual(correlation([1, 2, 3], [3, 2, 1]), -1)
        self.assertIsNone(correlation([1, 1, 1], [1, 2, 3]))
        self.assertEqual(ranks([2, 1, 2]), [2.5, 1, 2.5])
        with self.assertRaises(ValueError):
            correlation([1], [1, 2])

    def test_funding_coverage_validates_hour_buckets(self):
        points, quality = official_funding(dict(observations=[official_record()]))
        self.assertEqual(quality["par:BTC"]["missing_hour_buckets"], [])
        self.assertEqual(len(points["par:BTC"]), 3)

    def test_funding_digest_mismatch_is_not_silent(self):
        record = official_record()
        record["payload"][0]["fundingRate"] = "0.2"
        with self.assertRaises(ValueError):
            official_funding(dict(observations=[record]))

    def test_positive_funding_debits_long_and_credits_short(self):
        points, quality = official_funding(dict(observations=[official_record()]))
        segment = [Quote("par:BTC", t, 100, 100, 100, 0, 10, 10) for t in (1000, 3600000, 3601000)]
        times = [q.time_ms for q in segment]
        long = funding_cash(points["par:BTC"], quality["par:BTC"], segment, times, 1000, 3601000, 1, 90000)
        short = funding_cash(points["par:BTC"], quality["par:BTC"], segment, times, 1000, 3601000, -1, 90000)
        self.assertAlmostEqual(long, -.01)
        self.assertEqual(long, -short)

    def test_future_oracle_not_used_at_settlement(self):
        points, quality = official_funding(dict(observations=[official_record()]))
        segment = [Quote("par:BTC", 3600000, 100, 100, 100, 0, 10, 10),
                   Quote("par:BTC", 3601000, 100, 100, 999, 0, 10, 10)]
        value = funding_cash(points["par:BTC"], quality["par:BTC"], segment, [3600000, 3601000],
                             3600000, 3601000, 1, 90000)
        self.assertAlmostEqual(value, -.01)

    def test_no_settlement_in_interval_means_zero_cashflow(self):
        points, quality = official_funding(dict(observations=[official_record()]))
        self.assertEqual(funding_cash(points["par:BTC"], quality["par:BTC"], [], [], 1000, 2000, 1, 90000), 0)

    def test_missing_hour_or_stale_oracle_means_unknown(self):
        self.assertIsNone(funding_cash([(30, .001)], dict(missing_hour_buckets=[1]), [], [],
                                       1000, 3601000, 1, 90000))
        points, quality = official_funding(dict(observations=[official_record()]))
        old = Quote("par:BTC", 1000, 100, 100, 100, 0, 1, 1)
        self.assertIsNone(funding_cash(points["par:BTC"], quality["par:BTC"], [old], [1000],
                                       1000, 3601000, 1, 90000))

    def test_recent_trade_tail_is_not_complete_history(self):
        evidence = dict(observations=[dict(request=dict(type="recentTrades", coin="par:BTC"), error=None,
                                          payload=[dict(coin="par:BTC", time=3500, px="99", side="B"),
                                                   dict(coin="par:BTC", time=999999, px="99", side="A")])])
        result = official_trade_check(fixture(), evidence, CONFIG)["par:BTC"]
        self.assertFalse(result["complete_history"])
        self.assertEqual(result["within_local_window"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["matches"][0]["last_local_received_ms"], 3000)


if __name__ == "__main__":
    unittest.main()
