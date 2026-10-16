import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from decimal import ROUND_UP, Decimal, localcontext
from pathlib import Path

from market_risk_support import ASOF, STEP, bar, fixture

from perpetual_alpha.contracts import RiskSnapshot
from perpetual_alpha.historical.contracts import PRICE_UNITS
from perpetual_alpha.historical.store import HistoricalStore
from perpetual_alpha.journal import canonical, digest
from perpetual_alpha.market_risk import RiskConfig, estimate, freeze, replay, seal


class MarketRiskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.sqlite3"
        self.store, self.config, self.inputs = fixture(self.path)

    def changed(self, action, *, index=0):
        inputs = copy.deepcopy(self.inputs)
        h = inputs["histories"][index]
        action(h)
        h["payload_digest"] = digest({k: v for k, v in h.items() if k != "payload_digest"})
        return estimate(inputs)

    def test_hand_calculated_sample_covariance_not_population(self):
        result = estimate(self.inputs)
        s = RiskSnapshot.from_dict(result["risk_snapshot"])
        self.assertEqual(s.status, "ready")
        self.assertEqual(s.sample_count, 3)
        self.assertEqual(s.covariance, (("0.01", "0.01"), ("0.01", "0.01")))
        self.assertEqual(s.volatilities, {"par:BTC": "0.1", "par:ETH": "0.1"})
        self.assertEqual(result["diagnostics"]["correlation"], [["1", "1"], ["1", "1"]])
        self.assertEqual(s.estimation_window_ms, 3 * STEP)
        self.assertEqual((s.units, s.annualization), ("simple-return-per-interval", "none"))

    def test_singular_matrix_is_not_inverted_or_regularized(self):
        r = estimate(self.inputs)
        self.assertEqual(r["risk_snapshot"]["status"], "ready")
        self.assertEqual(r["diagnostics"]["matrix_invertibility"], "not_assumed")
        self.assertFalse(r["diagnostics"]["execution_authorized"])

    def test_zero_variance_has_null_correlation(self):
        _, _, inputs = fixture(Path(self.temp.name) / "flat.sqlite3", closes=[["1"] * 4, ["2"] * 4])
        r = estimate(inputs)
        self.assertEqual(r["risk_snapshot"]["status"], "ready")
        self.assertEqual(r["diagnostics"]["correlation"], [[None, None], [None, None]])
        self.assertEqual(r["diagnostics"]["zero_volatility_symbols"], list(self.config.symbols))

    def test_negative_correlation(self):
        _, _, inputs = fixture(Path(self.temp.name) / "negative.sqlite3",
                               closes=[["100", "110", "99", "99"], ["100", "90", "99", "99"]])
        r = estimate(inputs)
        self.assertEqual(r["risk_snapshot"]["covariance"][0][1], "-0.01")
        self.assertEqual(r["diagnostics"]["correlation"][0][1], "-1")

    def test_order_is_preserved_with_unequal_variances(self):
        _, _, inputs = fixture(Path(self.temp.name) / "ordered.sqlite3", symbols=("par:ETH", "par:BTC"),
                               closes=[["100", "120", "96", "96"], ["100", "110", "99", "99"]])
        s = estimate(inputs)["risk_snapshot"]
        self.assertEqual(s["symbols"], ["par:ETH", "par:BTC"])
        self.assertEqual(s["covariance"], [["0.04", "0.02"], ["0.02", "0.01"]])

    def test_ten_assets_including_fx_do_not_require_usd_valuation(self):
        symbols = tuple(PRICE_UNITS)
        _, _, inputs = fixture(Path(self.temp.name) / "ten.sqlite3", symbols, [["10", "11", "9.9", "9.9"]] * 10)
        s = estimate(inputs)["risk_snapshot"]
        self.assertEqual(s["status"], "ready")
        self.assertEqual(len(s["covariance"]), 10)
        self.assertEqual(s["symbols"], list(symbols))

    def test_single_asset(self):
        _, _, inputs = fixture(Path(self.temp.name) / "one.sqlite3", ("par:BTC",), [["100", "110", "99", "99"]])
        self.assertEqual(estimate(inputs)["risk_snapshot"]["covariance"], [["0.01"]])

    def test_insufficient_minimum_is_unavailable(self):
        self.inputs["config"]["min_samples"] = 4
        s = estimate(self.inputs)["risk_snapshot"]
        self.assertEqual(s["errors"], ["INSUFFICIENT_SAMPLES"])
        self.assertEqual((s["volatilities"], s["covariance"]), ({}, []))

    def test_quality_flags_checked_even_with_ready_status(self):
        for key, value in (("gaps", [{"start_ms": 0, "end_ms": STEP}]), ("invalid", [{"reason": "bad"}]),
                           ("truncated", True), ("closed_only", False), ("not_yet_closed_records", 1),
                           ("available_records", 2), ("expected_records", 2)):
            with self.subTest(key=key):
                s = self.changed(lambda h: h.update({key: value}))["risk_snapshot"]
                self.assertEqual(s["status"], "missing")
                self.assertEqual(s["covariance"], [])

    def test_actual_empty_database(self):
        empty = HistoricalStore(Path(self.temp.name) / "empty.sqlite3", clock=lambda: ASOF)
        s = estimate(freeze(empty, self.config, ASOF, synthetic=False))["risk_snapshot"]
        self.assertEqual(s["status"], "missing")
        self.assertFalse(s["simulated"])

    def test_missing_asset_does_not_shrink_universe(self):
        h = self.inputs["histories"][1]
        h.update(records=[], status="missing", available_records=0)
        h["payload_digest"] = digest({k: v for k, v in h.items() if k != "payload_digest"})
        s = estimate(self.inputs)["risk_snapshot"]
        self.assertEqual(s["symbols"], list(self.config.symbols))
        self.assertEqual(s["sample_count"], 0)

    def test_stale_plus_gap_is_not_ready(self):
        s = self.changed(lambda h: h.update(status="stale", gaps=[{"start_ms": 0, "end_ms": STEP}]))["risk_snapshot"]
        self.assertEqual(s["status"], "stale")
        self.assertTrue(any(e.startswith("INCOMPLETE") for e in s["errors"]))

    def test_invalid_records_fail_closed_without_pairwise_deletion(self):
        changes = [lambda r: r.pop(), lambda r: r.reverse(), lambda r: r[1].update(open_ms=r[0]["open_ms"]),
                   lambda r: r[0].update(close="0"), lambda r: r[0].update(close="NaN"),
                   lambda r: r[0].update(close="Infinity"), lambda r: r[0].update(close="1e101"),
                   lambda r: r[0].update(close=1), lambda r: r[0].update(received_ms=ASOF+1),
                   lambda r: r[0].update(available_ms=ASOF+1), lambda r: r[0].update(revision_seq=999),
                   lambda r: r[0].update(received_ms=0), lambda r: r[0].update(close_ms=ASOF+1)]
        for change in changes:
            with self.subTest(change=change):
                s = self.changed(lambda h: change(h["records"]))["risk_snapshot"]
                self.assertNotEqual(s["status"], "ready")
                self.assertEqual(s["covariance"], [])

    def test_evidence_identity_and_digest_rejected(self):
        for field, value in (("source", "other"), ("symbol", "par:GOLD"), ("asof_ms", ASOF+1),
                             ("snapshot_seq", 99), ("price_unit", "invented"), ("interval", "5m"), ("end_ms", 0)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.changed(lambda h: h.update({field: value}))
        self.inputs["histories"][0]["records"][0]["close"] = "999"
        with self.assertRaises(ValueError):
            estimate(self.inputs)

    def test_exact_asof_and_late_arrival(self):
        old = freeze(self.store, self.config, ASOF - 1, synthetic=True)
        self.assertEqual(estimate(old)["risk_snapshot"]["status"], "missing")
        self.assertEqual(estimate(self.inputs)["risk_snapshot"]["status"], "ready")

    def test_revision_after_pin_does_not_change_replay_or_old_window(self):
        evidence = seal(self.inputs)
        self.store.clock = lambda: ASOF + 1
        self.store.record_candles("revision", "par:BTC", "1m", 2 * STEP, ASOF,
                                  [bar("par:BTC", 2 * STEP, "50")], ASOF + 1)
        self.assertEqual(replay(evidence), evidence["result"])
        self.assertEqual(seal(freeze(self.store, self.config, ASOF, synthetic=True)), evidence)
        new = seal(freeze(self.store, self.config, ASOF+1, synthetic=True))
        self.assertNotEqual(new["input_digest"], evidence["input_digest"])

    def test_replay_survives_source_database_removal(self):
        evidence = seal(self.inputs)
        self.path.unlink()
        self.assertEqual(replay(json.loads(canonical(evidence))), evidence["result"])

    def test_digest_and_recomputed_result_checked(self):
        for part in ("inputs", "result"):
            e = seal(self.inputs)
            e[part]["unexpected"] = True
            with self.assertRaises(ValueError):
                replay(e)
        e = seal(self.inputs)
        e["result"]["diagnostics"]["execution_authorized"] = True
        e["result_digest"] = digest(e["result"])
        e["artifact_digest"] = digest({k: v for k, v in e.items() if k != "artifact_digest"})
        with self.assertRaisesRegex(ValueError, "differs"):
            replay(e)

    def test_unknown_model_and_shape_rejected(self):
        for change in ({"model_version": "new"}, {"schema_version": "new"}, {"extra": True},
                       {"synthetic": "true"}, {"histories": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                estimate({**self.inputs, **change})

    def test_decimal_context_is_independent(self):
        original = seal(self.inputs)
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = 6, ROUND_UP
            self.assertEqual(seal(self.inputs), original)

    def test_input_not_mutated(self):
        before = copy.deepcopy(self.inputs)
        seal(self.inputs)
        self.assertEqual(before, self.inputs)

    def test_source_flag_does_not_authorize_execution(self):
        self.inputs["synthetic"] = False
        r = estimate(self.inputs)
        self.assertFalse(r["risk_snapshot"]["simulated"])
        self.assertFalse(r["diagnostics"]["execution_authorized"])

    def test_config_rejects_bad_or_unbounded_inputs(self):
        for change in ({"symbols": []}, {"symbols": ["par:BTC", "par:BTC"]}, {"symbols": ["par:UNKNOWN"]},
                       {"interval": "1w"}, {"return_count": True}, {"return_count": 1001}, {"return_count": 1},
                       {"min_samples": 1}, {"max_age_ms": -1}, {"max_age_ms": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                RiskConfig.from_dict({**self.config.to_dict(), **change})
        with self.assertRaises(ValueError):
            RiskConfig.from_dict({**self.config.to_dict(), "extra": True})

    def test_cli_independent_process_and_exclusive_output(self):
        output = Path(self.temp.name) / "risk.json"
        cmd = [sys.executable, "-m", "perpetual_alpha.risk_replay", "freeze", "--database", str(self.path),
               "--asof-ms", str(ASOF), "--symbols", *self.config.symbols, "--interval", "1m", "--return-count", "3",
               "--min-samples", "3", "--max-age-ms", str(STEP), "--source-kind", "synthetic", "--output", str(output)]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)
        old = output.read_bytes()
        self.assertNotEqual(subprocess.run(cmd, capture_output=True, timeout=15).returncode, 0)
        self.assertEqual(output.read_bytes(), old)
        self.path.unlink()
        done = subprocess.run([sys.executable, "-m", "perpetual_alpha.risk_replay", "replay", "--input", str(output)],
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(json.loads(done.stdout)["execution_authorized"])

    def test_nontrivial_matrix_passes_existing_contract(self):
        _, _, inputs = fixture(Path(self.temp.name) / "nontrivial.sqlite3", ("par:BTC", "par:ETH", "par:GOLD"),
                               [["101", "107", "96", "112"], ["33", "32", "35", "36"], ["17", "21", "19", "18"]])
        s = RiskSnapshot.from_dict(estimate(inputs)["risk_snapshot"])
        self.assertEqual(s.status, "ready")
        self.assertEqual(s.covariance[0][1], s.covariance[1][0])
        for i, name in enumerate(s.symbols):
            self.assertLess(abs(Decimal(s.covariance[i][i]) - Decimal(s.volatilities[name]) ** 2), Decimal("1e-12"))

    def test_actual_gap_not_forward_filled(self):
        path = Path(self.temp.name) / "gap.sqlite3"
        store = HistoricalStore(path, clock=lambda: ASOF)
        for s in self.config.symbols:
            rows = [bar(s, i * STEP, "100") for i in (2, 3, 5)]
            store.record_candles("gap:"+s, s, "1m", 2*STEP, ASOF, rows, ASOF)
        s = estimate(freeze(store, self.config, ASOF, synthetic=True))["risk_snapshot"]
        self.assertEqual(s["status"], "missing")
        self.assertEqual(s["covariance"], [])

    def test_actual_invalid_revision_never_falls_back(self):
        self.store.clock = lambda: ASOF + 1
        bad = bar("par:BTC", 3*STEP, "100")
        bad["h"] = "1"
        self.store.record_candles("quarantined", "par:BTC", "1m", 2*STEP, ASOF, [bad], ASOF+1)
        current = freeze(self.store, self.config, ASOF+1, synthetic=True)
        self.assertEqual(estimate(current)["risk_snapshot"]["status"], "missing")
        self.assertEqual(estimate(self.inputs)["risk_snapshot"]["status"], "ready")

    def test_freeze_fences_write_between_asset_reads(self):
        read = self.store.read_candles
        fired = []

        def racing(*args, **kwargs):
            if not fired:
                fired.append(True)
                # Same wall clock: only snapshot_seq/anchor separates this later observation.
                self.store.record_candles("racing", "par:ETH", "1m", 2*STEP, ASOF,
                                          [bar("par:ETH", 2*STEP, "10")], ASOF)
            return read(*args, **kwargs)

        self.store.read_candles = racing
        self.assertEqual(freeze(self.store, self.config, ASOF, synthetic=True), self.inputs)
        self.assertNotEqual(freeze(self.store, self.config, ASOF, synthetic=True), self.inputs)

    def test_numeric_extreme_returns_error_without_repair(self):
        _, _, inputs = fixture(Path(self.temp.name) / "extreme.sqlite3", ("par:BTC",),
                               [["1e-100", "3e99", "7e100", "2e-100"]])
        s = estimate(inputs)["risk_snapshot"]
        self.assertEqual(s["status"], "error")
        self.assertEqual(s["errors"], ["NUMERICAL_VALIDATION_FAILED"])
        self.assertEqual(s["covariance"], [])

    def test_freshness_boundary_and_unaligned_asof(self):
        cfg = RiskConfig(self.config.symbols, "1m", 3, 3, 100)
        ready = freeze(self.store, cfg, ASOF+100, synthetic=True)
        stale = freeze(self.store, cfg, ASOF+101, synthetic=True)
        self.assertEqual(estimate(ready)["risk_snapshot"]["status"], "ready")
        self.assertEqual(estimate(stale)["risk_snapshot"]["status"], "stale")

    def test_minimum_two_returns(self):
        config = RiskConfig(("par:BTC",), "1m", 2, 2, STEP)
        s = estimate(freeze(self.store, config, ASOF, synthetic=True))["risk_snapshot"]
        self.assertEqual(s["sample_count"], 2)
        self.assertEqual(Decimal(s["covariance"][0][0]), Decimal("0.005"))

    def test_readonly_freeze_does_not_create_missing_database(self):
        missing = Path(self.temp.name) / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            HistoricalStore(missing, read_only=True)
        self.assertFalse(missing.exists())

    def test_maximum_window_on_nondefault_interval(self):
        step, asof = 300000, 1001*300000
        store = HistoricalStore(Path(self.temp.name) / "large.sqlite3", clock=lambda: asof)
        rows = [{"t": i*step, "T": (i+1)*step-1, "s": "par:BTC", "i": "5m", "o": str(100+i%7),
                 "h": str(100+i%7), "l": str(100+i%7), "c": str(100+i%7), "v": "1", "n": 1}
                for i in range(1001)]
        store.record_candles("large-synthetic", "par:BTC", "5m", 0, asof, rows, asof)
        cfg = RiskConfig(("par:BTC",), "5m", 1000, 1000, 0)
        result = seal(freeze(store, cfg, asof, synthetic=True))
        s = replay(result)["risk_snapshot"]
        self.assertEqual(s["status"], "ready")
        self.assertEqual((s["sample_count"], s["return_interval_ms"]), (1000, step))


if __name__ == "__main__":
    unittest.main()
