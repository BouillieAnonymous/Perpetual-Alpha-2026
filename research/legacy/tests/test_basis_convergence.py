import unittest
from dataclasses import replace

from perpetual_alpha.basis_convergence import (
    Quote,
    StudyConfig,
    decomposition,
    distribution,
    events_for_segment,
    km_summary,
    liquidation,
    prepare,
    run,
)


def quote(now, mid=100, oracle=100, size=10):
    bid, ask = str(mid - .001), str(mid + .001)
    return dict(symbol="par:BTC", event_ms=now - 10, received_ms=now,
                context_received_ms=now - 20, bid=bid, ask=ask, oracle=str(oracle), funding="0",
                book=dict(coin="par:BTC", time=now - 10,
                          levels=[[dict(px=bid, sz=str(size))], [dict(px=ask, sz=str(size))]]))


CONFIG = StudyConfig(thresholds_bps=(20,), bands_bps=(2,), horizons_s=(1, 3, 5, 10),
                     max_gap_ms=2000, latency_ms=100, horizon_tolerance_ms=1000, bootstrap_repeats=0)


def events(rows, config=CONFIG):
    return run(rows, config)["events"]


class EpisodeTests(unittest.TestCase):
    def test_continuous_deviation_is_one_episode(self):
        rows = [quote(1000), quote(2000, 99), quote(3000, 99), quote(4000, 99), quote(5000)]
        result = events(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["convergence_ms"], 3000)
        self.assertEqual(result[0]["observed_detection_bracket_ms"], [2000, 3000])

    def test_reset_required_for_new_episode(self):
        rows = [quote(1000), quote(2000, 99), quote(3000), quote(4000, 101), quote(5000, 101)]
        result = events(rows)
        self.assertEqual([e["side"] for e in result], ["long", "short"])

    def test_initial_deviation_is_left_truncated(self):
        self.assertEqual(events([quote(1000, 99), quote(2000, 99), quote(3000)]), [])

    def test_opposite_jump_is_not_an_observed_band_hit(self):
        result = events([quote(1000), quote(2000, 99), quote(3000, 101), quote(4000, 101)])
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["convergence_ms"])

    def test_gap_censors_and_does_not_create_spurious_onset(self):
        result = events([quote(1000), quote(2000, 99), quote(3000, 99), quote(7000, 99), quote(8000)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["censor_reason"], "gap")
        self.assertEqual(result[0]["follow_ms"], 1000)

    def test_invalid_reference_breaks_segment(self):
        bad = quote(4000)
        bad["context_received_ms"] = 5000
        segments, quality = prepare([quote(1000), quote(2000, 99), quote(3000, 99), bad, quote(5000)], CONFIG)
        self.assertEqual(quality["invalid_rows"], 1)
        self.assertEqual(len(segments), 2)
        batch, _ = events_for_segment(*segments[0], 20, 2, CONFIG, 4000)
        self.assertEqual(batch[0]["censor_reason"], "invalid_row")

    def test_gone_signal_keeps_convergence_but_has_no_trade(self):
        event = events([quote(1000), quote(2000, 99), quote(3000)])[0]
        self.assertEqual(event["convergence_ms"], 1000)
        self.assertEqual(event["entry_status"], "deviation_gone_at_next_quote")
        self.assertIsNone(event["breakeven_ms"])

    def test_depth_limit_cannot_be_bypassed(self):
        event = events([quote(1000), quote(2000, 99), quote(3000, 99, size=.00001), quote(4000)])[0]
        self.assertEqual(event["entry_status"], "insufficient_entry_depth")

    def test_oracle_catches_down_but_trade_loses(self):
        event = events([quote(1000), quote(2000, 99), quote(3000, 99), quote(4000, 98, 98),
                        quote(5000, 98, 98)])[0]
        self.assertEqual(event["convergence_ms"], 2000)
        self.assertLess(event["at_convergence"]["base_pnl"], 0)
        self.assertLess(event["decomposition"]["oracle_move_bps"], 0)
        self.assertGreater(event["decomposition"]["basis_move_bps"], 0)
        self.assertEqual(event["delayed_convergence_exit"]["time_ms"], 5000)

    def test_event_end_is_censored_not_a_forced_exit(self):
        event = events([quote(1000), quote(2000, 99), quote(3000, 99)])[0]
        self.assertEqual(event["censor_reason"], "data_end")
        self.assertIsNone(event["convergence_ms"])
        self.assertIsNone(event["at_convergence"])

    def test_observation_beyond_horizon_does_not_count_as_early_hit(self):
        config = replace(CONFIG, horizons_s=(1, 3))
        event = events([quote(1000), quote(2000, 99), quote(3000, 99), quote(4000, 99),
                        quote(5000, 99), quote(6000)], config)[0]
        self.assertEqual(event["follow_ms"], 3000)
        self.assertIsNone(event["convergence_ms"])

    def test_quantity_uses_verified_step_and_adverse_loss_includes_entry_cost(self):
        event = events([quote(1000), quote(2000, 99), quote(3000, 99), quote(4000), quote(5000)])[0]
        self.assertAlmostEqual(event["quantity"] / .0001, round(event["quantity"] / .0001), places=6)
        self.assertLess(event["mae_to_convergence_bps"], -18)

    def test_horizon_does_not_fill_forward_or_ignore_depth(self):
        event = events([quote(1000), quote(2000, 99), quote(3000, 99),
                        quote(4000, 99), quote(5000, size=.00001)])[0]
        self.assertNotIn("3", event["horizon_exits"])
        self.assertNotIn("5", event["horizon_exits"])

    def test_duplicate_or_nonfinite_inputs(self):
        with self.assertRaises(ValueError):
            prepare([quote(1000), quote(1000)], CONFIG)
        row = quote(1000)
        row["oracle"] = "NaN"
        _, quality = prepare([row], CONFIG)
        self.assertEqual(quality["invalid_rows"], 1)


class StatisticalTests(unittest.TestCase):
    def test_km_accounts_for_censoring(self):
        records = [dict(convergence_ms=1000, follow_ms=5000), dict(convergence_ms=None, follow_ms=2000),
                   dict(convergence_ms=3000, follow_ms=5000)]
        result = km_summary(records, "convergence_ms", (1, 3, 5))
        self.assertAlmostEqual(result["horizons"][0]["km_probability"], 1 / 3)
        self.assertEqual(result["horizons"][1]["km_probability"], 1)
        self.assertEqual(result["median_s"], 3)
        self.assertEqual(result["horizons"][1]["censoring_bound_high"], 1)

    def test_tied_censor_is_removed_after_hits(self):
        records = [dict(convergence_ms=1000, follow_ms=1000), dict(convergence_ms=None, follow_ms=1000)]
        self.assertEqual(km_summary(records, "convergence_ms", (1,))["horizons"][0]["km_probability"], .5)

    def test_no_extrapolation_or_mean_of_only_successes(self):
        result = km_summary([dict(convergence_ms=None, follow_ms=1000)], "convergence_ms", (1, 3))
        self.assertIsNone(result["median_s"])
        self.assertIsNone(result["mean_among_observed_hits_s"])
        self.assertIsNone(result["horizons"][1]["km_probability"])
        self.assertEqual(result["restricted_mean_s"], 1)

    def test_empty_statistics_do_not_claim_zero_probability(self):
        self.assertIsNone(km_summary([], "convergence_ms", (60,))["horizons"][0]["km_probability"])
        self.assertIsNone(distribution([])["mean"])

    def test_exact_decomposition_and_short_sign(self):
        start = Quote("par:BTC", 1, 99, 99, 100, 0, 1, 1)
        end = Quote("par:BTC", 2, 98, 98, 98, 0, 1, 1)
        long, short = decomposition(start, end, 1), decomposition(start, end, -1)
        self.assertAlmostEqual(long["mid_move_bps"], long["oracle_move_bps"] + long["basis_move_bps"])
        self.assertEqual(long["mid_move_bps"], -short["mid_move_bps"])

    def test_gross_cost_components_and_double_cost_share_quotes(self):
        q = Quote("par:BTC", 1, 100, 100, 100, .01, 1, 1)
        value = liquidation(q, q, 1, 1, .1, CONFIG)
        self.assertEqual(value["gross_pnl"], 0)
        self.assertAlmostEqual(value["base_pnl"],
                               -value["base_fee"] - value["base_slippage"] - value["base_funding"])
        self.assertLess(value["double_pnl"], value["base_pnl"])

    def test_configuration_rejects_invalid_grids_and_costs(self):
        for kwargs in (dict(thresholds_bps=(1,)), dict(horizons_s=(5, 1)), dict(fee_bps=-1),
                       dict(quantity_step="0"), dict(bootstrap_repeats=-1)):
            with self.assertRaises(ValueError):
                StudyConfig(**kwargs)

    def test_group_denominators_and_reproducibility(self):
        config = replace(CONFIG, bootstrap_repeats=10)
        rows = [quote(1000), quote(2000, 99), quote(3000, 99), quote(4000), quote(5000)]
        result = run(rows, config)
        self.assertEqual(result, run(rows, config))
        metrics = result["groups"][0]["result"]["metrics"]
        self.assertEqual(metrics["entered_convergence_ms"]["n"], metrics["profit_ms"]["n"])


if __name__ == "__main__":
    unittest.main()
