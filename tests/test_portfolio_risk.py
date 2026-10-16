import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from decimal import ROUND_UP, Decimal, localcontext
from pathlib import Path

from portfolio_support import fixture

from perpetual_alpha.contracts import ForecastBatch, RiskSnapshot, RiskVerdict
from perpetual_alpha.historical.contracts import PRICE_UNITS
from perpetual_alpha.interfaces import Decision
from perpetual_alpha.journal import Journal, canonical, digest
from perpetual_alpha.portfolio_policies import BudgetPortfolio
from perpetual_alpha.portfolio_simulation import MockGateBridge, run
from perpetual_alpha.risk_gate import SimulationRiskGate
from perpetual_alpha.risk_policy import SimulationPolicy, exposure_metrics, validate_state
from perpetual_alpha.runtime.supervisor import RiskSupervisor
from perpetual_alpha.simulation import MockExecution


class PortfolioRiskTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture()
        self.state, self.now = self.f["state"], self.f["now_ms"]

    def policy(self):
        return SimulationPolicy.from_dict(self.f["policy"])

    def plan(self):
        return BudgetPortfolio(self.policy()).construct(ForecastBatch.from_dict(self.f["forecast"]), self.state,
                                                        self.now, self.f["decision_id"])

    def gate(self, *, resume=True):
        supervisor = RiskSupervisor(self.policy())
        if resume:
            supervisor.resume(self.state, self.now)
        return SimulationRiskGate(self.policy(), supervisor)

    def hold(self, s, q):
        self.state["account"]["positions"].append({"symbol": s, "quantity": q, "mark_price": "10"})

    def order(self, s, q, side="buy", reduce_only=False):
        self.state["account"]["open_orders"].append({"order_id": "manual-"+str(len(self.state["account"]["open_orders"])),
                                                   "symbol": s, "side": side, "remaining_quantity": q, "reduce_only": reduce_only})

    def test_fixed_capital_quantity_and_existing_contracts(self):
        p = self.plan()
        self.assertEqual(p["decision"]["targets"], [{"symbol": "par:BTC", "quantity": "5"}])
        self.assertEqual(Decision.from_dict(p["decision"]).targets[0].quantity, Decimal(5))
        r = self.gate().check(p, self.state, self.now)
        self.assertEqual(RiskVerdict.from_dict(r["verdict"]).status, "approved")
        self.assertFalse(r["execution_authorized"])

    def test_portfolio_shrinks_for_sigma_budget(self):
        self.f["policy"]["sigma_cap_usd"] = "3"
        p = self.plan()
        self.assertEqual(Decimal(p["decision"]["targets"][0]["quantity"]), Decimal(3))
        self.assertLess(Decimal(p["sizing"]["scale"]), 1)
        self.assertEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")

    def test_retained_unpredicted_holding_consumes_budget(self):
        self.hold("par:ETH", "4")
        self.f["policy"]["gross_cap_usd"] = "60"
        p = self.plan()
        self.assertEqual(p["decision"]["targets"], [{"symbol": "par:BTC", "quantity": "2.0"}])
        self.assertEqual(p["sizing"]["metrics"]["gross_usd"], "60.0")
        self.assertEqual(self.state["account"]["positions"][0]["quantity"], "4")

    def test_all_pending_orders_including_reduce_only_are_counted(self):
        self.hold("par:ETH", "2")
        self.order("par:ETH", "3", "sell", True)
        self.order("par:ETH", "1", "buy")
        m = exposure_metrics(self.state, self.policy(), self.now, {})
        self.assertEqual(m["usd_bounds"]["par:ETH"], ["-10", "30"])
        self.assertEqual(m["gross_usd"], "30")

    def test_order_asset_not_in_forecast_cannot_be_ignored(self):
        self.order("par:ETH", "8")
        self.f["policy"]["gross_cap_usd"] = "100"
        self.f["policy"]["margin_buffer_usd"] = "0"
        p = self.plan()
        self.assertLessEqual(Decimal(p["decision"]["targets"][0]["quantity"]), 2)

    def test_unknown_exposure_and_missing_risk_coverage_block(self):
        for symbol in ("par:UNKNOWN", "par:GOLD"):
            with self.subTest(symbol=symbol):
                self.state["account"]["positions"] = [{"symbol": symbol, "quantity": "1", "mark_price": "10"}]
                with self.assertRaises(ValueError):
                    self.plan()

    def test_neutral_abstain_and_uncovered_do_not_flatten(self):
        self.hold("par:BTC", "2")
        self.hold("par:ETH", "1")
        for status, direction in (("ready", "neutral"), ("abstain", None)):
            with self.subTest(status=status):
                self.f["forecast"]["items"][0].update(status=status, direction=direction)
                self.assertEqual(self.plan()["decision"]["targets"], [])
        self.f["forecast"]["items"] = []
        self.assertEqual(self.plan()["decision"]["targets"], [])

    def test_error_item_blocks_whole_batch(self):
        self.f["forecast"]["items"][0].update(status="error", direction=None)
        with self.assertRaises(ValueError):
            self.plan()

    def test_ttl_is_bounded_by_forecast(self):
        self.f["forecast"]["items"][0]["expires_ms"] = self.now+5000
        self.assertEqual(self.plan()["decision"]["expires_ms"], self.now+5000)

    def test_step_rounding_and_substep_budget_do_not_flatten(self):
        self.f["policy"]["asset_budgets_usd"]["par:BTC"] = "12.34"
        self.assertEqual(self.plan()["decision"]["targets"][0]["quantity"], "1.2")
        self.hold("par:BTC", "1")
        self.f["policy"]["asset_budgets_usd"]["par:BTC"] = "0.1"
        self.assertEqual(self.plan()["decision"]["targets"], [])

    def test_dust_is_not_silently_rounded(self):
        self.hold("par:BTC", "0.05")
        with self.assertRaisesRegex(ValueError, "dust"):
            self.plan()

    def test_reversal_and_explicit_exit_need_separate_simulation_policy(self):
        self.hold("par:BTC", "2")
        self.f["forecast"]["items"][0]["direction"] = "down"
        with self.assertRaisesRegex(ValueError, "Reversal"):
            self.plan()
        self.f["policy"]["allow_reversals"] = True
        self.assertEqual(self.plan()["decision"]["targets"][0]["quantity"], "-5")

    def test_gate_rejects_explicit_zero_without_exit_policy(self):
        self.hold("par:BTC", "2")
        p = self.plan()
        p["decision"]["targets"][0]["quantity"] = "0"
        self.assertNotEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")
        self.f["policy"]["allow_explicit_exits"] = True
        p = self.plan()
        p["decision"]["targets"][0]["quantity"] = "0"
        self.assertEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")

    def test_gate_ignores_fake_portfolio_metrics_and_does_not_mutate_targets(self):
        p = self.plan()
        p["sizing"] = {"metrics": {"breaches": []}, "scale": "1"}
        p["decision"]["targets"][0]["quantity"] = "20"
        before = copy.deepcopy(p)
        r = self.gate().check(p, self.state, self.now)
        self.assertEqual(r["verdict"]["status"], "rejected")
        self.assertEqual(before, p)
        self.assertEqual(r["verdict"]["decision_digest"], digest(p["decision"]))

    def test_gate_independently_enforces_variance(self):
        self.f["policy"]["sigma_cap_usd"] = "3"
        p = self.plan()
        p["decision"]["targets"][0]["quantity"] = "5"
        r = self.gate().check(p, self.state, self.now)
        self.assertEqual(r["verdict"]["reason_code"], "RISK_LIMIT")
        self.assertIn("SIGMA", r["metrics"]["breaches"])

    def test_gate_checks_transition_corners_not_only_final_hedge(self):
        self.f["policy"]["asset_budgets_usd"]["par:ETH"] = "50"
        self.f["policy"]["allow_reversals"] = True
        self.f["policy"]["sigma_cap_usd"] = "5"
        self.f["policy"]["margin_buffer_usd"] = "0"
        self.state["control"]["symbols"] = ["par:BTC", "par:ETH"]
        self.hold("par:BTC", "5")
        self.hold("par:ETH", "-5")
        self.f["forecast"]["items"][0]["direction"] = "down"
        second = copy.deepcopy(self.f["forecast"]["items"][0])
        second.update(symbol="par:ETH", direction="up")
        self.f["forecast"]["items"].append(second)
        p = self.plan()
        p["decision"]["targets"] = [{"symbol": "par:BTC", "quantity": "-5"}, {"symbol": "par:ETH", "quantity": "5"}]
        r = self.gate().check(p, self.state, self.now)
        self.assertEqual(r["metrics"]["sigma_bound_usd"], "10.0")
        self.assertEqual(r["verdict"]["status"], "rejected")

    def test_gross_net_leverage_margin_and_asset_limits_shrink(self):
        for field, value in (("gross_cap_usd", "20"), ("net_cap_usd", "20"), ("leverage_cap", "0.2"),
                             ("margin_buffer_usd", "80")):
            with self.subTest(field=field):
                self.f = fixture()
                self.state = self.f["state"]
                self.f["policy"][field] = value
                p = self.plan()
                self.assertLessEqual(Decimal(p["decision"]["targets"][0]["quantity"]), 2)
                self.assertEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")
        self.f["policy"]["asset_caps_usd"]["par:BTC"] = "10"
        self.assertLessEqual(Decimal(self.plan()["decision"]["targets"][0]["quantity"]), 1)

    def test_existing_breach_is_supervised_without_auto_exit(self):
        self.hold("par:ETH", "11")
        with self.assertRaises(ValueError):
            self.plan()
        report = RiskSupervisor(self.policy()).inspect(self.state, self.now)
        self.assertTrue(report["paused"])
        self.assertIn("GROSS", report["current_failures"])
        self.assertEqual(report["automatic_actions"], [])

    def test_supervisor_starts_paused_and_explicit_resume_is_required(self):
        p = self.plan()
        g = self.gate(resume=False)
        self.assertEqual(g.check(p, self.state, self.now)["verdict"]["status"], "deferred")
        g.supervisor.resume(self.state, self.now)
        self.assertEqual(g.check(p, self.state, self.now)["verdict"]["status"], "approved")

    def test_health_failure_latches_even_after_recovery(self):
        s = RiskSupervisor(self.policy())
        s.resume(self.state, self.now)
        self.state["health"]["execution"] = False
        self.assertTrue(s.inspect(self.state, self.now)["paused"])
        with self.assertRaises(ValueError):
            s.resume(self.state, self.now)
        self.state["health"]["execution"] = True
        self.assertTrue(s.inspect(self.state, self.now)["paused"])
        self.assertFalse(s.resume(self.state, self.now)["paused"])
        p, g = self.plan(), self.gate()
        self.state["health"]["execution"] = False
        self.assertEqual(g.check(p, self.state, self.now)["verdict"]["status"], "deferred")
        self.state["health"]["execution"] = True
        self.assertEqual(g.check(p, self.state, self.now)["verdict"]["status"], "deferred")
        g.supervisor.resume(self.state, self.now)
        self.assertEqual(g.check(p, self.state, self.now)["verdict"]["status"], "approved")

    def test_supervisor_operates_without_forecast_or_agent(self):
        self.f.pop("forecast")
        r = RiskSupervisor(self.policy()).inspect(self.state, self.now+60001)
        self.assertEqual(r["current_failures"], ["STATE_UNAVAILABLE"])
        self.assertEqual(r["automatic_actions"], [])

    def test_drawdown_and_session_loss_independently_pause(self):
        self.state["account"]["equity_usd"] = "79"
        r = RiskSupervisor(self.policy()).inspect(self.state, self.now)
        self.assertIn("DRAWDOWN", r["current_failures"])
        self.assertIn("SESSION_LOSS", r["current_failures"])

    def test_real_account_observation_never_becomes_synthetic_ready(self):
        root = Path(__file__).resolve().parents[1]
        real_shape = json.loads((root / "docs/examples/account-observation.synthetic.json").read_text(encoding="utf-8"))["snapshot"]
        # Even a synthetic PR5 fixture retains the actual observation contract and risk_ready=false.
        with self.assertRaises(ValueError):
            validate_state(real_shape, self.policy(), self.now)
        self.state["account"] = real_shape
        self.assertEqual(RiskSupervisor(self.policy()).inspect(self.state, self.now)["current_failures"], ["STATE_UNAVAILABLE"])

    def test_incomplete_or_unknown_account_assertions_fail_closed(self):
        for key in self.state["assertions"]:
            with self.subTest(key=key):
                s = copy.deepcopy(self.state)
                s["assertions"][key] = False
                with self.assertRaises(ValueError):
                    validate_state(s, self.policy(), self.now)

    def test_observed_status_alone_is_not_readiness(self):
        self.state["account"]["status"] = "observed"
        with self.assertRaises(ValueError):
            self.plan()

    def test_state_policy_or_metadata_change_invalidates_approval(self):
        for part, key, value in (("control", "epoch", 2), ("control", "producer_version", "2"),
                                 ("health", "market_data", False), ("account", "available_margin_usd", "1")):
            with self.subTest(part=part, key=key):
                p, g = self.plan(), self.gate()
                changed = copy.deepcopy(self.state)
                changed[part][key] = value
                self.assertNotEqual(g.check(p, changed, self.now)["verdict"]["status"], "approved")
        p, g = self.plan(), self.gate()
        changed = copy.deepcopy(self.state)
        changed["market"]["par:BTC"]["quantity_step"] = "3"
        self.assertNotEqual(g.check(p, changed, self.now)["verdict"]["status"], "approved")
        g.policy["gross_cap_usd"] = "99"
        self.assertNotEqual(g.check(p, self.state, self.now)["verdict"]["status"], "approved")

    def test_consumed_watermark_and_shadow_have_no_control_authority(self):
        p, g = self.plan(), self.gate()
        self.state["control"]["consumed_asof_ms"] = self.now
        self.assertNotEqual(g.check(p, self.state, self.now)["verdict"]["status"], "approved")
        with self.assertRaises(ValueError):
            self.plan()
        self.state["control"]["consumed_asof_ms"] = None
        self.state["control"]["role"] = "shadow"
        with self.assertRaises(ValueError):
            self.plan()

    def test_forecast_expiry_horizon_identity_and_error_checked_at_gate(self):
        for field, value in (("expires_ms", self.now+1000), ("horizon_ms", 123), ("symbol", "par:ETH")):
            p = self.plan()
            p["forecast"]["items"][0][field] = value
            p["forecast_digest"] = digest(p["forecast"])
            self.assertNotEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")
        p = self.plan()
        p["forecast"]["items"][0].update(status="error", direction=None)
        p["forecast_digest"] = digest(p["forecast"])
        self.assertNotEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")

    def test_decision_expired_future_or_wrong_step_is_not_modified(self):
        for field, value in (("expires_ms", self.now), ("created_ms", self.now+1)):
            p = self.plan()
            p["decision"][field] = value
            if field == "expires_ms":
                # Contract itself rejects inverted intervals.
                with self.assertRaises(ValueError):
                    self.gate().check(p, self.state, self.now)
            else:
                self.assertNotEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")
        p = self.plan()
        p["decision"]["targets"][0]["quantity"] = "1.01"
        self.assertNotEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")

    def test_risk_model_interval_samples_and_source_are_explicit(self):
        for key, value in (("model_version", "other"), ("return_interval_ms", 1), ("sample_count", 2), ("simulated", False)):
            with self.subTest(key=key):
                s = copy.deepcopy(self.state)
                s["risk"][key] = value
                with self.assertRaises(ValueError):
                    validate_state(s, self.policy(), self.now)

    def test_account_market_and_risk_age_rechecked_at_consumption(self):
        p, g = self.plan(), self.gate()
        r = g.check(p, self.state, self.now+10000)
        self.assertEqual(r["verdict"]["reason_code"], "EXPIRED")
        # Total age is checked against now, not only against a slightly older state cutoff.
        self.state["account"]["observed_ms"] = self.now-60000
        with self.assertRaises(ValueError):
            validate_state(self.state, self.policy(), self.now+1)

    def test_configuration_has_no_live_mode_or_silent_defaults(self):
        for key, value in (("mode", "live"), ("gross_cap_usd", "NaN"), ("leverage_cap", "0"),
                           ("drawdown_cap", "2"), ("allow_reversals", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                SimulationPolicy.from_dict({**self.f["policy"], key: value})
        p = dict(self.f["policy"])
        p.pop("margin_buffer_usd")
        with self.assertRaises(ValueError):
            SimulationPolicy.from_dict(p)

    def test_decimal_context_and_input_immutability(self):
        before = copy.deepcopy(self.f)
        proposal = self.plan()
        with localcontext() as c:
            c.prec, c.rounding = 6, ROUND_UP
            self.assertEqual(self.plan(), proposal)
        self.assertEqual(before, self.f)

    def test_simulation_end_to_end_with_existing_mock_and_fresh_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/"sim.sqlite3"
            r = run(self.f, p, resume_supervisor=True)
            self.assertEqual(r["approval"]["verdict"]["status"], "approved")
            self.assertEqual(r["execution_report"]["status"], "completed")
            self.assertFalse(r["real_execution_authorized"])
            c = sqlite3.connect(p)
            self.assertEqual(c.execute("select count(*) from decisions").fetchone()[0], 1)
            c.close()
            with self.assertRaises(FileExistsError):
                run(self.f, p, resume_supervisor=True)

    def test_simulation_cannot_run_without_explicit_supervisor_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            r = run(self.f, Path(folder)/"sim.sqlite3")
            self.assertEqual(r["execution_report"]["status"], "rejected")

    def test_mock_rechecks_and_retains_original_idempotency(self):
        p, gate = self.plan(), self.gate()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        journal = Journal(Path(folder.name)/"journal.sqlite3")
        self.addCleanup(journal.connection.close)
        bridge = MockGateBridge(gate, p, self.state)
        execution = MockExecution(journal, bridge, bridge.market, lambda: RiskSnapshot.from_dict(self.state["risk"]),
                                  lambda: self.now, ("synthetic-agent", "1"))
        execution.initialize_account(self.state["account"])
        decision = Decision.from_dict(p["decision"])
        first = execution.apply(decision)
        self.assertEqual(first.to_dict(), execution.apply(decision).to_dict())
        self.assertEqual(journal.connection.execute("select count(*) from decisions").fetchone()[0], 1)

    def test_mock_refuses_state_changed_after_preapproval(self):
        p, gate = self.plan(), self.gate()
        bridge = MockGateBridge(gate, p, self.state)
        account = copy.deepcopy(self.state["account"])
        account["open_orders"] = [{"order_id": "late", "symbol": "par:ETH", "side": "buy", "remaining_quantity": "1", "reduce_only": False}]
        v = bridge.check(Decision.from_dict(p["decision"]), account, bridge.market(), RiskSnapshot.from_dict(self.state["risk"]), self.now)
        self.assertNotEqual(v.status, "approved")

    def test_cli_independent_process_uses_only_synthetic_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/"input.json"
            source.write_text(canonical(self.f), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "perpetual_alpha.portfolio_simulation", "--fixture", str(source),
                                "--journal", str(Path(folder)/"sim.sqlite3"), "--resume-supervisor"],
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["execution_report"]["status"], "completed")

    def test_full_ten_asset_exposure_and_corner_budget(self):
        symbols = list(PRICE_UNITS)
        self.state["risk"].update(symbols=symbols, covariance=[["0.01"]*10 for _ in symbols],
                                  volatilities={s: "0.1" for s in symbols})
        self.f["policy"]["asset_caps_usd"] = {s: "100" for s in symbols}
        for s in symbols:
            self.state["market"][s] = {"usd_per_unit": "10", "quantity_step": "0.1", "observed_ms": self.now,
                                        "event_ms": self.now, "valuation_source": "synthetic-fixture"}
            if s != "par:BTC":
                self.hold(s, "0.1")
                self.order(s, "0.1", "sell")
        p = self.plan()
        r = self.gate().check(p, self.state, self.now)
        self.assertEqual(r["verdict"]["status"], "approved")
        self.assertEqual(set(r["metrics"]["usd_bounds"]), set(symbols))
        self.assertEqual(Decimal(r["metrics"]["gross_usd"]), Decimal(59))

    def test_risk_matrix_order_is_not_assumed_to_be_alphabetical(self):
        self.state["risk"].update(symbols=["par:ETH", "par:BTC"], covariance=[["0.04", "0.02"], ["0.02", "0.01"]],
                                  volatilities={"par:ETH": "0.2", "par:BTC": "0.1"})
        self.hold("par:ETH", "1")
        r = self.gate().check(self.plan(), self.state, self.now)
        self.assertEqual(Decimal(r["metrics"]["sigma_bound_usd"]), Decimal(7))

    def test_limits_at_boundary_and_zero_remaining_orders(self):
        self.f["policy"]["gross_cap_usd"] = "50"
        self.f["policy"]["sigma_cap_usd"] = "5"
        self.order("par:ETH", "0")
        self.assertEqual(self.gate().check(self.plan(), self.state, self.now)["verdict"]["status"], "approved")
        self.state["account"]["equity_usd"] = "80"
        r = RiskSupervisor(self.policy()).inspect(self.state, self.now)
        self.assertNotIn("DRAWDOWN", r["current_failures"])
        self.assertNotIn("SESSION_LOSS", r["current_failures"])

    def test_unknown_inflight_manual_exposure_blocks_even_with_empty_orders(self):
        p, g = self.plan(), self.gate()
        self.state["assertions"]["inflight_orders_known"] = False
        r = g.check(p, self.state, self.now)
        self.assertNotEqual(r["verdict"]["status"], "approved")
        self.assertEqual(self.state["account"]["open_orders"], [])

    def test_synthetic_valuation_not_inferred_from_account_mark(self):
        self.state["market"]["par:BTC"]["valuation_source"] = "hyperliquid"
        with self.assertRaises(ValueError):
            self.plan()

    def test_supervisor_restart_returns_to_paused(self):
        first = RiskSupervisor(self.policy())
        first.resume(self.state, self.now)
        first.inspect(self.state, self.now)
        restarted = RiskSupervisor(self.policy())
        self.assertTrue(restarted.inspect(self.state, self.now)["paused"])

    def test_tampered_forecast_digest_and_unpredicted_target_rejected(self):
        p = self.plan()
        p["forecast_digest"] = "0"*64
        self.assertNotEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")
        p = self.plan()
        p["decision"]["targets"].append({"symbol": "par:ETH", "quantity": "1"})
        self.assertNotEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")
        p = self.plan()
        p["control_epoch"] = True
        self.assertNotEqual(self.gate().check(p, self.state, self.now)["verdict"]["status"], "approved")

    def test_health_check_does_not_require_agent_heartbeat(self):
        # The supervision API accepts no Agent/Forecast input and has no Agent liveness override.
        s = RiskSupervisor(self.policy())
        self.assertFalse(s.resume(self.state, self.now)["paused"])
        self.order("par:ETH", "20")
        self.assertTrue(s.inspect(self.state, self.now)["paused"])


if __name__ == "__main__":
    unittest.main()
