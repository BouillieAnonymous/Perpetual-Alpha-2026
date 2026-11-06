import unittest
from dataclasses import replace

from perpetual_alpha.basis_convergence import StudyConfig, prepare
from perpetual_alpha.historical.contracts import PRICE_UNITS
from perpetual_alpha.multi_asset_basis import asset_config, normalize_rows, study


def metadata():
    return {"universe": [{"name": s, "szDecimals": i % 5, "maxLeverage": 10}
                         for i, s in enumerate(PRICE_UNITS)]}


def row(now, mid=100, symbol="par:USDJPY"):
    bid, ask = str(mid - .001), str(mid + .001)
    return {"symbol": symbol, "event_ms": now - 1, "received_ms": now,
            "context_received_ms": now - 2, "bid": bid, "ask": ask, "oracle": "100", "funding": "0",
            "book": {"coin": symbol, "time": now - 1,
                     "levels": [[{"px": bid, "sz": "1000"}], [{"px": ask, "sz": "1000"}]]}}


CONFIG = StudyConfig(thresholds_bps=(20,), bands_bps=(2,), horizons_s=(1, 3, 5),
                     max_gap_ms=2000, latency_ms=100, horizon_tolerance_ms=1000, bootstrap_repeats=0)


class MultiAssetTests(unittest.TestCase):
    def test_every_asset_supported_and_precision_from_metadata(self):
        for i, symbol in enumerate(PRICE_UNITS):
            report = study([row(1000, symbol=symbol), row(2000, 99, symbol), row(3000, 99, symbol),
                            row(4000, symbol=symbol)], metadata(), config=CONFIG)
            item = report["assets"][symbol]
            self.assertEqual(item["metadata"]["quantity_step"], format(__import__("decimal").Decimal(1).scaleb(-(i % 5)), "f"))
            self.assertEqual(item["pnl_status"], "valuation_unverified")
            self.assertEqual(len(item["convergence"]["events"]), 1)
            self.assertEqual(item["convergence"]["events"][0]["entry_status"], "valuation_unverified")
            self.assertEqual(item["direction"]["observations"][0]["original_net_bps"], None)

    def test_linear_multiplier_preserves_basis_and_converts_notional_prices(self):
        original = row(1000)
        batch, _ = normalize_rows([original], "par:USDJPY", 10)
        self.assertEqual(batch[0]["bid"], "999.990")
        self.assertEqual(original["bid"], "99.999")
        segments, _ = prepare(batch, replace(CONFIG, symbols=("par:USDJPY",), pnl_enabled=False))
        self.assertAlmostEqual(segments[0][0][0].basis, 0)

    def test_restart_and_invalid_join_break_followup(self):
        rows = [row(1000), row(2000, 99), row(3000, 99), row(4000)]
        for i, r in enumerate(rows):
            r["collection_session"] = "one" if i < 3 else "two"
        batch, _ = normalize_rows(rows, "par:USDJPY", 1)
        segments, diag = prepare(batch, replace(CONFIG, symbols=("par:USDJPY",), pnl_enabled=False))
        self.assertEqual(len(segments), 2)
        self.assertEqual(diag["connection_breaks"], 1)
        report = study(rows, metadata(), config=CONFIG)
        self.assertIsNone(report["assets"]["par:USDJPY"]["convergence"]["events"][0]["convergence_ms"])

    def test_missing_assets_and_short_probe_not_claimed_as_history(self):
        report = study([row(1000)], metadata(), config=CONFIG)
        self.assertEqual(report["assets"]["par:USDJPY"]["data_status"], "short_probe_only")
        self.assertEqual(report["assets"]["par:BTC"]["data_status"], "insufficient_history")
        self.assertIsNone(report["assets"]["par:BTC"]["convergence"])

    def test_valuation_evidence_and_precision_are_separate_gates(self):
        from perpetual_alpha.historical.contracts import metadata as parse
        meta = parse(metadata())["par:USDJPY"]
        config, _ = asset_config("par:USDJPY", meta, {}, CONFIG)
        self.assertFalse(config.pnl_enabled)
        proof = {"par:USDJPY": {"verified": True, "model": "linear_quote", "multiplier": "0.01",
                                "evidence_url": "https://example.test/fixture", "settlement_asset": "USDPR"}}
        config, multiplier = asset_config("par:USDJPY", meta, proof, CONFIG)
        self.assertTrue(config.pnl_enabled)
        self.assertEqual(str(multiplier), "0.01")
        proof["par:USDJPY"]["model"] = "inverse"
        self.assertFalse(asset_config("par:USDJPY", meta, proof, CONFIG)[0].pnl_enabled)

    def test_direct_engine_rejects_implicit_btc_lot_for_other_assets(self):
        with self.assertRaises(ValueError):
            replace(CONFIG, symbols=("par:USDJPY",))

    def test_invalid_price_precision_remains_descriptive_not_executable(self):
        original = row(1000)
        batch, _ = normalize_rows([original], "par:USDJPY", 1, sz_decimals=5)
        self.assertTrue(batch[0]["execution_rules_invalid"])
        segments, _ = prepare(batch, replace(CONFIG, symbols=("par:USDJPY",), pnl_enabled=False))
        self.assertFalse(segments[0][0][0].executable)

    def test_volatility_regime_never_uses_signal_or_future_price(self):
        from perpetual_alpha.multi_asset_basis import past_regimes
        rows = [row(1000 + i * 30000) for i in range(12)]
        cfg = replace(CONFIG, symbols=("par:USDJPY",), pnl_enabled=False, max_gap_ms=90000)
        before = past_regimes(prepare(rows, cfg)[0])
        rows[-1] = row(rows[-1]["received_ms"], 10000)
        after = past_regimes(prepare(rows, cfg)[0])
        key = ("par:USDJPY", rows[-1]["received_ms"])
        self.assertEqual(before[key], after[key])
        self.assertEqual(before[key], "low_lt_10bps")

    def test_archive_keeps_missing_asset_results_unavailable(self):
        import csv
        import importlib.util
        import tempfile
        from pathlib import Path
        source = Path(__file__).resolve().parents[1] / "scripts/archive_multi_asset_basis.py"
        spec = importlib.util.spec_from_file_location("archive_multi_asset_basis", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = study([], metadata(), config=CONFIG)
        result.update(inputs={}, method_sources={})
        with tempfile.TemporaryDirectory() as folder:
            module.archive(result, Path(folder))
            with (Path(folder) / "comparison.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 10)
            self.assertTrue(all(r["net_bps"] == "" for r in rows))
            self.assertTrue(all(r["convergence_10m"] == "" for r in rows))
