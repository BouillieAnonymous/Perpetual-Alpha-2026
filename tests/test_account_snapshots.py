"""Scoped readonly account observations, unknown margin and recoverable SQLite evidence."""
import copy
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from account_support import WALLET, Clock, FakeInfoClient, order, position

from perpetual_alpha.accounts.capture import AccountCapture
from perpetual_alpha.accounts.client import AccountInfoClient, wallet_address
from perpetual_alpha.accounts.normalize import normalize
from perpetual_alpha.accounts.store import AccountStore
from perpetual_alpha.context import validate_account
from perpetual_alpha.historical.contracts import PRICE_UNITS
from perpetual_alpha.journal import digest

ROOT = Path(__file__).resolve().parents[1]


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.client = FakeInfoClient(self.clock)

    def capture(self, capture_id="synthetic-one"):
        return AccountCapture(self.client, clock=self.clock).capture(WALLET, capture_id)

    def test_complete_scoped_observation_is_not_risk_ready(self):
        bundle, snapshot = self.capture()
        self.assertEqual(len(bundle["observations"]), 21)
        self.assertEqual(snapshot["status"], "observed")
        self.assertEqual(snapshot["consistency"], "bracket_matched_non_atomic")
        self.assertFalse(snapshot["atomic"])
        self.assertFalse(snapshot["risk_ready"])
        self.assertFalse(snapshot["global_account_complete"])
        self.assertFalse(snapshot["team_ownership_verified"])
        self.assertEqual(len(snapshot["exposures"]), 10)

    def test_spot_balance_not_perp_value_not_available_margin(self):
        _, snapshot = self.capture()
        self.assertEqual(snapshot["collateral"]["total"], "1000")
        self.assertEqual(snapshot["collateral"]["unheld_balance"], "900")
        self.assertEqual(snapshot["collateral"]["available_after_maintenance"], "700")
        self.assertEqual(snapshot["venue_perp_summary"]["withdrawable"], "42")
        self.assertEqual(snapshot["venue_perp_summary"]["marginSummary"]["accountValue"], "1")
        self.assertIsNone(snapshot["equity_usd"])
        self.assertIsNone(snapshot["available_margin_usd"])

    def test_all_assets_buying_power_retains_source_order_and_units(self):
        _, snapshot = self.capture()
        self.assertEqual(set(snapshot["buying_power"]), set(PRICE_UNITS))
        self.assertEqual(snapshot["buying_power"]["par:USDJPY"]["available_to_trade"], ["123", "124"])
        self.assertEqual(snapshot["buying_power"]["par:BTC"]["max_trade_sizes"], ["1", "2"])
        self.assertFalse(snapshot["buying_power"]["par:BTC"]["direction_mapping_verified"])
        self.assertEqual(snapshot["buying_power"]["par:BTC"]["available_unit"], "venue-availableToTrade")

    def test_external_position_to_prediction_is_preserved_and_no_zero_reset(self):
        _, snapshot = self.capture()
        self.assertEqual({p["symbol"] for p in snapshot["positions"]}, {"par:BTC", "par:HYPE"})
        self.assertEqual(snapshot["exposures"]["par:HYPE"]["position_quantity"], "-1")
        self.assertEqual(snapshot["exposures"]["par:GOLD"]["position_quantity"], "0")

    def test_orders_reduce_only_and_trigger_all_in_observed_bounds(self):
        _, snapshot = self.capture()
        self.assertEqual(snapshot["exposures"]["par:BTC"]["minimum_quantity"], "1")
        self.assertEqual(snapshot["exposures"]["par:BTC"]["maximum_quantity"], "2")
        self.assertEqual(snapshot["exposures"]["par:ETH"]["maximum_quantity"], "3")
        self.assertEqual(snapshot["open_orders"][0]["ownership"], "unattributed")
        self.assertEqual(snapshot["open_orders"][1]["status"], "observed_open")
        self.assertIsNone(snapshot["exposures"]["par:BTC"]["notional_usd"])

    def test_unknown_symbol_is_preserved_instead_of_filtered(self):
        self.client.payloads["clearinghouseState"]["assetPositions"].append(position("par:UNKNOWN", "-7"))
        self.client.payloads["frontendOpenOrders"].append(order(99, "xyz:OTHER", "B", "4"))
        _, snapshot = self.capture()
        self.assertEqual(snapshot["exposures"]["par:UNKNOWN"]["position_quantity"], "-7")
        self.assertEqual(snapshot["exposures"]["xyz:OTHER"]["pending_buy_quantity"], "4")
        self.assertIn("UNSUPPORTED_EXPOSURE:par:UNKNOWN", snapshot["issues"])
        self.assertEqual(snapshot["status"], "partial")

    def test_fx_position_does_not_assume_usd_quantity_times_price(self):
        self.client.payloads["clearinghouseState"]["assetPositions"].append(position("par:USDJPY", "100"))
        _, snapshot = self.capture()
        fx = next(p for p in snapshot["positions"] if p["symbol"] == "par:USDJPY")
        self.assertEqual(fx["price_unit"], "JPY/USD")
        self.assertIsNone(fx["notional_usd"])
        self.assertIsNone(fx["valuation_multiplier"])

    def test_agent_role_stops_without_following_owner(self):
        self.client.payloads["userRole"] = {"role": "agent", "data": {"user": "0x" + "2" * 40}}
        _, snapshot = self.capture()
        self.assertEqual(self.client.calls, [("userRole", None)])
        self.assertEqual(snapshot["status"], "unavailable")
        self.assertEqual(snapshot["wallet"], WALLET)
        self.assertFalse(snapshot["par_positions_complete"])

    def test_missing_role_and_role_query_failure_are_unavailable(self):
        for failure in (False, True):
            self.client = FakeInfoClient(self.clock)
            self.client.payloads["userRole"] = {"role": "missing"}
            if failure:
                self.client.failures.add(("userRole", 1))
            self.assertEqual(self.capture()[1]["status"], "unavailable")

    def test_mode_change_does_not_modify_account(self):
        self.client.transforms["userAbstraction", 2] = lambda _: "disabled"
        _, snapshot = self.capture()
        self.assertEqual(snapshot["status"], "partial")
        self.assertIn("UNSUPPORTED_OR_UNKNOWN_ACCOUNT_MODE", snapshot["issues"])
        self.assertEqual({kind for kind, _ in self.client.calls}, {"userRole", "userAbstraction", "spotClearinghouseState",
                         "clearinghouseState", "frontendOpenOrders", "metaAndAssetCtxs", "activeAssetData"})

    def test_position_change_is_reported_non_atomic_and_unknown_bounds(self):
        self.client.transforms["clearinghouseState", 2] = lambda row: {**row, "assetPositions": [position(quantity="3")]}
        _, snapshot = self.capture()
        self.assertEqual(snapshot["consistency"], "changed_or_incomplete")
        self.assertEqual(snapshot["exposures"]["par:BTC"]["position_quantity"], "3")
        self.assertIsNone(snapshot["exposures"]["par:BTC"]["maximum_quantity"])

    def test_disappearing_order_is_unresolved_not_canceled_or_filled(self):
        self.client.transforms["frontendOpenOrders", 2] = lambda _: []
        _, snapshot = self.capture()
        self.assertEqual(snapshot["order_changes"]["disappeared_unresolved"], ["1", "2"])
        self.assertEqual(snapshot["status"], "partial")
        self.assertIsNone(snapshot["exposures"]["par:BTC"]["minimum_quantity"])
        self.assertEqual(snapshot["inflight_order_exposure"], "unknown-no-intent-ledger")

    def test_endpoint_list_reordering_is_not_false_state_change(self):
        self.client.transforms["frontendOpenOrders", 2] = lambda rows: list(reversed(rows))
        self.client.transforms["clearinghouseState", 2] = lambda row: {**row, "assetPositions": list(reversed(row["assetPositions"]))}
        self.assertEqual(self.capture()[1]["consistency"], "bracket_matched_non_atomic")

    def test_orders_failure_never_becomes_empty_complete(self):
        self.client.failures.add(("frontendOpenOrders", 2))
        _, snapshot = self.capture()
        self.assertFalse(snapshot["par_orders_complete"])
        self.assertIsNone(snapshot["exposures"]["par:BTC"]["pending_buy_quantity"])
        self.assertIsNone(snapshot["exposures"]["par:BTC"]["maximum_quantity"])

    def test_position_failure_never_becomes_flat(self):
        self.client.failures.add(("clearinghouseState", 2))
        _, snapshot = self.capture()
        self.assertFalse(snapshot["par_positions_complete"])
        self.assertIsNone(snapshot["exposures"]["par:GOLD"]["position_quantity"])

    def test_balance_failure_never_falls_back_to_perp_margin(self):
        self.client.failures.add(("spotClearinghouseState", 2))
        _, snapshot = self.capture()
        self.assertIsNone(snapshot["collateral"])
        self.assertIsNone(snapshot["equity_usd"])
        self.assertFalse(snapshot["balances_complete"])

    def test_collateral_name_and_token_identity_both_required(self):
        self.client.payloads["spotClearinghouseState"]["balances"][0]["token"] = 999
        _, snapshot = self.capture()
        self.assertIsNone(snapshot["collateral"])
        self.assertIn("COLLATERAL_IDENTITY_UNVERIFIED", snapshot["issues"])

    def test_missing_balance_is_unknown_not_zero(self):
        self.client.payloads["spotClearinghouseState"]["balances"] = []
        _, snapshot = self.capture()
        self.assertIsNone(snapshot["collateral"])

    def test_negative_collateral_remains_debt_not_clamped_to_zero(self):
        self.client.payloads["spotClearinghouseState"]["balances"][0].update(total="-2", hold="0")
        _, snapshot = self.capture()
        self.assertEqual(snapshot["collateral"]["total"], "-2")
        self.assertEqual(snapshot["collateral"]["unheld_balance"], "-2")

    def test_invalid_order_and_unknown_reduce_only_make_coverage_incomplete(self):
        for change in ({"reduceOnly": None}, {"sz": "9"}, {"side": "X"}, {"sz": "NaN"}, {"timestamp": 999999}):
            with self.subTest(change=change):
                self.client = FakeInfoClient(self.clock)
                self.client.payloads["frontendOpenOrders"][0].update(change)
                self.assertFalse(self.capture()[1]["par_orders_complete"])

    def test_invalid_positions_and_duplicate_positions_make_coverage_incomplete(self):
        for rows in ([position(quantity="NaN")], [position(), position()], [position(quantity=2)]):
            self.client = FakeInfoClient(self.clock)
            self.client.payloads["clearinghouseState"]["assetPositions"] = rows
            self.assertFalse(self.capture()[1]["par_positions_complete"])

    def test_future_server_timestamp_is_not_current_valid_state(self):
        self.client.payloads["clearinghouseState"]["time"] = 999999
        _, snapshot = self.capture()
        self.assertFalse(snapshot["par_positions_complete"])

    def test_old_server_state_not_freshened_by_receiving_now(self):
        self.client.payloads["clearinghouseState"]["time"] = 1
        _, snapshot = self.capture()
        self.assertIn("PERP_STATE_STALE", snapshot["issues"])
        self.assertFalse(snapshot["par_positions_complete"])
        self.assertIsNone(snapshot["exposures"]["par:GOLD"]["position_quantity"])

    def test_capture_byte_budget_stops_further_network_requests(self):
        self.client.payloads["userRole"]["oversized"] = "x" * (8 * 1024 * 1024)
        _, snapshot = self.capture()
        self.assertEqual(len(self.client.calls), 1)
        self.assertIn("CAPTURE_BYTE_BUDGET_EXCEEDED", snapshot["issues"])

    def test_failed_active_asset_does_not_inherit_another_assets_buying_power(self):
        self.client.failures.add(("activeAssetData", 2))
        _, snapshot = self.capture()
        self.assertNotIn("par:ETH", snapshot["buying_power"])
        self.assertEqual(len(snapshot["buying_power"]), 9)

    def test_active_identity_mismatch_is_quarantined(self):
        self.client.transforms["activeAssetData", 1] = lambda row: {**row, "user": "0x" + "2" * 40}
        _, snapshot = self.capture()
        self.assertNotIn("par:BTC", snapshot["buying_power"])

    def test_receive_time_and_request_wallet_cannot_be_forged(self):
        bundle, _ = self.capture()
        row = bundle["observations"]["perp_after"]
        row["request"]["user"] = "0x" + "2" * 40
        self.assertFalse(normalize(bundle)["par_positions_complete"])
        row["request"]["user"] = WALLET
        row["received_ms"] = bundle["ended_ms"] + 1
        self.assertFalse(normalize(bundle)["par_positions_complete"])

    def test_capture_deadline_stops_new_network_requests(self):
        original = self.client.read

        def slow(*args, **kwargs):
            response = original(*args, **kwargs)
            self.clock.now += 1001
            return response

        self.client.read = slow
        bundle, snapshot = AccountCapture(self.client, clock=self.clock, max_capture_ms=1000).capture(WALLET, "deadline")
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(snapshot["status"], "partial")
        self.assertIn("CAPTURE_WINDOW_EXCEEDED", snapshot["issues"])
        self.assertTrue(any(row["error"] == "CAPTURE_DEADLINE" for row in bundle["observations"].values()))

    def test_account_observation_cannot_be_passed_to_legacy_demo_gate(self):
        _, snapshot = self.capture()
        with self.assertRaises(ValueError):
            validate_account(snapshot, self.clock(), 90000)

    def test_snapshot_digest_binds_financial_and_quality_fields(self):
        _, snapshot = self.capture()
        self.assertEqual(snapshot["payload_digest"], digest({k: v for k, v in snapshot.items() if k != "payload_digest"}))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "account.db"
        self.clock = Clock()
        self.bundle, _ = AccountCapture(FakeInfoClient(self.clock), clock=self.clock).capture(WALLET, "one")
        self.store = AccountStore(self.path, clock=self.clock)

    def test_version_wal_sync_and_reject_other_databases(self):
        with self.store.connection() as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(c.execute("PRAGMA synchronous").fetchone()[0], 2)
        other = self.path.parent / "runtime.db"
        c = sqlite3.connect(other)
        c.execute("CREATE TABLE jobs(x)")
        c.close()
        with self.assertRaises(ValueError):
            AccountStore(other)

    def test_asof_freshness_and_availability_are_independent(self):
        self.clock.now += 100
        self.store.save(self.bundle)
        self.assertEqual(self.store.latest(WALLET, self.bundle["ended_ms"], max_age_ms=90000)["status"], "missing")
        observed = self.store.latest(WALLET, self.clock(), max_age_ms=90000)
        self.assertEqual(observed["status"], "observed")
        self.assertEqual(self.store.latest(WALLET, self.clock() + 90000, max_age_ms=90000)["status"], "stale")
        self.assertFalse(observed["risk_ready"])

    def test_wallet_isolation(self):
        self.store.save(self.bundle)
        self.assertEqual(self.store.latest("0x" + "2" * 40, self.clock(), max_age_ms=90000)["status"], "missing")

    def test_same_id_replay_and_conflict_preserve_first_availability(self):
        one = self.store.save(self.bundle)
        self.clock.now += 100
        self.assertEqual(self.store.save(self.bundle), one)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM captures").fetchone()[0], 1)
        changed = copy.deepcopy(self.bundle)
        changed["observations"]["spot_after"]["payload"]["balances"][0]["total"] = "2"
        with self.assertRaises(ValueError):
            self.store.save(changed)

    def test_latest_failed_capture_does_not_fall_back_to_old_observed(self):
        self.store.save(self.bundle)
        self.clock.now += 10
        client = FakeInfoClient(self.clock)
        client.failures.add(("clearinghouseState", 2))
        failed, _ = AccountCapture(client, clock=self.clock).capture(WALLET, "two")
        self.store.save(failed)
        result = self.store.latest(WALLET, self.clock(), max_age_ms=90000)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["snapshot"]["par_positions_complete"])

    def test_read_only_restart_and_digest_corruption(self):
        self.store.save(self.bundle)
        reader = AccountStore(self.path, clock=self.clock, read_only=True)
        self.assertEqual(reader.latest(WALLET, self.clock(), max_age_ms=90000)["status"], "observed")
        with self.assertRaises(ValueError):
            reader.save(self.bundle)
        with self.store.connection() as c:
            c.execute("UPDATE captures SET snapshot='{}'")
        with self.assertRaises(ValueError):
            reader.latest(WALLET, self.clock(), max_age_ms=90000)

    def test_future_capture_and_clock_rollback_rejected(self):
        self.clock.now -= 1
        with self.assertRaises(ValueError):
            self.store.save(self.bundle)
        self.clock.now += 1
        self.store.save(self.bundle)
        self.clock.now -= 1
        with self.assertRaises(ValueError):
            self.store.save(self.bundle)

    def test_concurrent_same_capture_deduplicates(self):
        def write(_):
            return AccountStore(self.path, clock=self.clock).save(self.bundle)
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(len(list(pool.map(write, range(4)))), 4)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM captures").fetchone()[0], 1)

    def test_uncommitted_process_death_keeps_previous_evidence(self):
        self.store.save(self.bundle)
        code = '''
import os,sys
from perpetual_alpha.accounts.store import AccountStore
s=AccountStore(sys.argv[1])
with s.connection() as c:
    c.execute('BEGIN IMMEDIATE')
    c.execute('DELETE FROM captures')
    os._exit(77)
'''
        result = subprocess.run([sys._base_executable, "-c", code, str(self.path)], capture_output=True,
                                timeout=10, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
        self.assertEqual(result.returncode, 77)
        self.assertEqual(self.store.latest(WALLET, self.clock(), max_age_ms=90000)["status"], "observed")


class ClientTests(unittest.TestCase):
    def test_client_only_allows_fixed_read_queries_and_never_imports_signing(self):
        client = AccountInfoClient()
        calls = []
        with patch.object(client.transport, "_request", side_effect=lambda request: (calls.append(request) or {}, 100000)):
            client.read("clearinghouseState", WALLET)
            client.read("frontendOpenOrders", WALLET)
            client.read("activeAssetData", WALLET, coin="par:BTC")
            client.read("metaAndAssetCtxs")
            with self.assertRaises(ValueError):
                client.read("order", WALLET)
            with self.assertRaises(ValueError):
                client.read("activeAssetData", WALLET, coin="BTC")
        self.assertEqual(calls[0], {"type": "clearinghouseState", "user": WALLET, "dex": "par"})
        self.assertTrue(all("signature" not in request for request in calls))
        self.assertEqual(client.transport.timeout, 10)

    def test_invalid_wallet_rejected_before_network(self):
        for value in (None, "private-key", "0x1", WALLET + "?"):
            with self.assertRaises(ValueError):
                wallet_address(value)
        self.assertEqual(wallet_address(WALLET.upper().replace("0X", "0x")), WALLET)
