import copy
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from perpetual_alpha.context import SOURCE, ContextBuilder
from perpetual_alpha.contracts import Forecast, ForecastBatch, RiskSnapshot, RiskVerdict
from perpetual_alpha.dry_run import open_collector_readonly, run
from perpetual_alpha.forecasting import (
    MockAgentForecastProducer,
    MockRiskEstimator,
    RuleForecastProducer,
    checked_forecast,
)
from perpetual_alpha.interfaces import DataRequest, Decision, TargetPosition
from perpetual_alpha.journal import Journal
from perpetual_alpha.portfolio import FixedQuantityPortfolio, PortfolioUnavailable
from perpetual_alpha.research import (
    ExperimentRecord,
    ResearchRegistry,
    ResearchTask,
    StrategyApproval,
    StrategyCandidate,
)
from perpetual_alpha.risk import DemoRiskGate
from perpetual_alpha.schemas import CONTRACTS, schema_for
from perpetual_alpha.simulation import MockExecution
from perpetual_alpha.storage import Store

SYMBOLS = ("par:BTC", "par:ETH")


def account(now=1000, positions=()):
    return {"observed_ms": now, "status": "ready", "equity_usd": "1000", "available_margin_usd": "1000",
            "positions": [{"symbol": s, "quantity": q, "mark_price": "100"} for s, q in positions],
            "open_orders": []}


def quote(s="par:BTC", event=900, received=950, mark="100"):
    return {"symbol": s, "event_ms": event, "received_ms": received, "context_received_ms": received,
            "bid": "99", "ask": "101", "mark": mark, "oracle": "99", "funding": "0",
            "source": SOURCE, "schema_version": 1}


class NewContractTests(unittest.TestCase):
    def item(self, **kwargs):
        return Forecast(**{"symbol": "par:BTC", "asof_ms": 1000, "expires_ms": 2000,
                           "horizon_ms": 3600000, "status": "ready", "direction": "up", **kwargs})

    def test_forecast_optional_numbers_and_round_trip(self):
        batch = ForecastBatch("b", "c", "rule-demo", "0.1", 1000,
                              (self.item(prob_up="0.6", prob_down="0.3", forecast_vol="0.01"),
                               self.item(symbol="par:ETH", status="abstain", direction=None)))
        self.assertEqual(ForecastBatch.from_dict(batch.to_dict()), batch)
        for invalid in ({"prob_up": "60"}, {"prob_up": 0.5}, {"forecast_vol": "NaN"},
                        {"prob_up": "0.7", "prob_down": "0.4"}, {"expected_return": "-2"},
                        {"quantiles": {"0.1": "0.2", "0.9": "0.1"}},
                        {"status": "abstain"}, {"status": "error", "direction": None, "prob_up": "0.1"},
                        {"asof_ms": True}, {"horizon_ms": 0}, {"expires_ms": 1000}):
            with self.subTest(invalid=invalid), self.assertRaises((ValueError, ArithmeticError)):
                self.item(**invalid)

    def test_batch_rejects_duplicates_future_and_unknown_fields(self):
        batch = ForecastBatch("b", "c", "p", "v", 1000, (self.item(),))
        with self.assertRaises(ValueError):
            replace(batch, items=(self.item(), self.item()))
        with self.assertRaises(ValueError):
            replace(batch, created_ms=900)
        with self.assertRaises(ValueError):
            ForecastBatch.from_dict({**batch.to_dict(), "unknown": True})

    def test_minimal_forecast_does_not_require_optional_predictions(self):
        payload = {"symbol": "par:BTC", "asof_ms": 1000, "expires_ms": 2000,
                   "horizon_ms": 100, "status": "ready", "direction": "up", "evidence_refs": []}
        item = Forecast.from_dict(payload)
        self.assertIsNone(item.expected_return)
        self.assertIsNone(item.prob_up)

    def test_exported_schemas_match_contract_definitions(self):
        root = Path(__file__).resolve().parents[1]
        for cls in CONTRACTS:
            payload = json.loads((root / "docs/schemas" / (cls.__name__ + ".schema.json")).read_text(encoding="utf-8"))
            self.assertEqual(payload, schema_for(cls))
            self.assertFalse(payload["additionalProperties"])

    def test_risk_matrix_units_status_and_psd(self):
        risk = RiskSnapshot("r", 1000, "ready", "mock", "v", SYMBOLS, 200, 100, 2,
                            {s: "0.1" for s in SYMBOLS}, (("0.01", "0"), ("0", "0.01")))
        self.assertEqual(RiskSnapshot.from_dict(risk.to_dict()), risk)
        for invalid in ({"sample_count": 1}, {"units": "annualized"}, {"status": "missing"},
                        {"covariance": ("00", "00"), "volatilities": {s: "0" for s in SYMBOLS}},
                        {"covariance": (("0.01", "0.02"), ("0.02", "0.01"))}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                replace(risk, **invalid)
        with self.assertRaises(ValueError):
            RiskSnapshot("r", 1000, "ready", "mock", "v", (*SYMBOLS, "par:GOLD"), 200, 100, 2,
                         {s: "0.1" for s in (*SYMBOLS, "par:GOLD")},
                         (("0.01", "-0.009", "-0.009"), ("-0.009", "0.01", "-0.009"),
                          ("-0.009", "-0.009", "0.01")))


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.builder = ContextBuilder(self.store.connection)
        self.request = DataRequest("q", SOURCE, "quote", ("par:BTC",), ("bid", "ask", "mark"), 0, 1, 100)

    def tearDown(self):
        self.store.close()

    def dataset(self, request=None, now=1000):
        return self.builder.build((request or self.request,), "c", now)["datasets"]["q"]

    def test_receipt_and_event_time_prevent_lookahead(self):
        self.store.save(quote(event=900, received=950, mark="100"))
        self.store.save(quote(event=980, received=1100, mark="200"))
        self.store.save(quote(event=1200, received=1200, mark="300"))
        dataset = self.dataset()
        self.assertEqual(dataset["status"], "ready")
        self.assertEqual(dataset["records"][0]["data"]["mark"], "100")
        self.assertEqual(self.dataset(now=1101)["status"], "stale")

    def test_missing_unsupported_history_and_truncation(self):
        self.assertEqual(self.dataset()["status"], "missing")
        self.assertEqual(self.dataset(replace(self.request, kind="news"))["status"], "error")
        self.assertEqual(self.dataset(replace(self.request, fields=("unknown",)))["status"], "error")
        self.store.save(quote(event=700, received=750))
        self.store.save(quote(event=850, received=900))
        self.store.save(quote(event=950, received=980))
        dataset = self.dataset(replace(self.request, lookback_ms=200))
        self.assertEqual(dataset["status"], "ready")
        self.assertTrue(dataset["truncated"])
        self.assertEqual(dataset["records"][0]["event_ms"], 950)
        self.assertEqual(self.dataset(replace(self.request, lookback_ms=400))["status"], "missing")

    def test_future_account_and_feedback_are_not_exposed(self):
        context = self.builder.build((), "c", 1000, account(1001), ({"observed_ms": 1001},))
        self.assertIsNone(context["account"])
        self.assertEqual(context["execution_reports"], [])

    def test_closed_or_corrupt_store_is_explicit_error(self):
        self.store.save({**quote(), "bid": "NaN"})
        self.assertEqual(self.dataset()["status"], "error")
        self.store.close()
        self.assertEqual(self.dataset()["status"], "error")

    def test_collector_open_is_readonly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quotes.sqlite3"
            store = Store(str(path))
            store.save(quote())
            store.close()
            connection = open_collector_readonly(path)
            try:
                context = ContextBuilder(connection).build((self.request,), "c", 1000)
                self.assertEqual(context["datasets"]["q"]["status"], "ready")
                with self.assertRaises(Exception):
                    connection.execute("DELETE FROM snapshots")
            finally:
                connection.close()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        for s in SYMBOLS:
            self.store.save(quote(s))
        self.rule = RuleForecastProducer(SYMBOLS, 100, 100)
        self.agent = MockAgentForecastProducer(SYMBOLS, 100, 100, ("par:ETH",))
        self.context = ContextBuilder(self.store.connection).build(self.rule.requirements(), "c", 1000, account())
        self.risk = MockRiskEstimator(SYMBOLS, 100).estimate(self.context)
        self.policy = FixedQuantityPortfolio({s: "0.1" for s in SYMBOLS}, 100, 100, 100, "demo")

    def tearDown(self):
        self.store.close()

    def construct(self, batch=None, risk=None, context=None, now=1000):
        return self.policy.construct(batch or self.agent.forecast(self.context), risk or self.risk,
                                     context or self.context, now, "d")

    def test_same_context_shadow_comparison_and_abstention(self):
        rule, agent = checked_forecast(self.rule, self.context), checked_forecast(self.agent, self.context)
        self.assertEqual(rule.context_id, agent.context_id)
        self.assertEqual([i.status for i in rule.items], ["ready", "ready"])
        self.assertEqual([i.status for i in agent.items], ["ready", "abstain"])
        self.assertEqual([t.symbol for t in self.construct(agent).targets], ["par:BTC"])

    def test_neutral_abstain_error_and_missing_assets_do_not_exit(self):
        batch = self.rule.forecast(self.context)
        for state, direction in (("ready", "neutral"), ("abstain", None), ("error", None)):
            item = replace(batch.items[0], status=state, direction=direction)
            self.assertEqual(self.construct(replace(batch, items=(item,))).targets, ())
        down = replace(batch.items[0], direction="down")
        self.assertEqual(self.construct(replace(batch, items=(down,))).targets[0].quantity, Decimal("-0.1"))

    def test_unavailable_account_data_risk_and_expiry_block(self):
        bad_context = copy.deepcopy(self.context)
        bad_context["account"] = None
        cases = [{"context": bad_context}, {"now": 1100},
                 {"risk": replace(self.risk, asof_ms=899)},
                 {"risk": replace(self.risk, return_interval_ms=50)}]
        bad_context = copy.deepcopy(self.context)
        bad_context["datasets"]["par:BTC_quotes"]["status"] = "missing"
        cases.append({"context": bad_context})
        batch = self.agent.forecast(self.context)
        cases.append({"batch": replace(batch, items=(replace(batch.items[0], horizon_ms=50), batch.items[1]))})
        for case in cases:
            with self.subTest(case=case), self.assertRaises(PortfolioUnavailable):
                self.construct(**case)

    def test_adapter_failure_and_wrong_evidence_are_error_forecasts(self):
        with patch.object(self.agent, "forecast", side_effect=TimeoutError):
            batch = checked_forecast(self.agent, self.context)
            self.assertTrue(all(i.status == "error" and i.direction is None for i in batch.items))
        original = self.agent.forecast(self.context)
        invalid = replace(original, items=(replace(original.items[0], evidence_refs=("unregistered",)),))
        with patch.object(self.agent, "forecast", return_value=invalid):
            self.assertEqual(checked_forecast(self.agent, self.context).items[0].status, "error")


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "journal.sqlite3"
        self.journal = Journal(self.path)
        self.now = 1000
        self.risk = RiskSnapshot("r", 1000, "ready", "mock", "v", SYMBOLS, 200, 100, 2,
                                 {s: "0.1" for s in SYMBOLS}, (("0.01", "0"), ("0", "0.01")))
        self.market = {s: {"observed_ms": 1000, "event_ms": 1000, "mark": "100", "bid": "99", "ask": "101"}
                       for s in SYMBOLS}
        self.gate = DemoRiskGate({s: "1" for s in SYMBOLS}, "100", 100, "demo")
        self.executor = self.new_executor()
        self.executor.initialize_account(account(positions=(("par:BTC", "0.1"), ("par:ETH", "0.2"))))

    def new_executor(self):
        return MockExecution(self.journal, self.gate, lambda: self.market, lambda: self.risk,
                             lambda: self.now, ("rule-demo", "0.1"))

    def tearDown(self):
        self.journal.close()
        self.directory.cleanup()

    def decision(self, id="d", quantities=(("par:BTC", "0.3"),)):
        return Decision(id, "c", "rule-demo", "0.1", 1000, 1100,
                        tuple(TargetPosition(s, Decimal(q)) for s, q in quantities), "SIMULATED")

    def test_absolute_target_delta_omitted_assets_and_restart_idempotency(self):
        decision = self.decision()
        report = self.executor.apply(decision)
        self.assertEqual(report.status, "completed")
        self.assertEqual(report.items[0].filled_quantity, Decimal("0.2"))
        self.assertEqual(self.executor.account()["positions"][1]["quantity"], "0.2")
        self.journal.close()
        self.journal = Journal(self.path)
        self.executor = self.new_executor()
        before = len(self.journal.events())
        self.now = 1200  # persisted retry returns prior completion, even after expiry
        self.assertEqual(self.executor.apply(decision), report)
        self.assertEqual(len(self.journal.events()), before)

    def test_conflicting_ids_reject_without_overwriting_original(self):
        completed = self.executor.apply(self.decision())
        conflict = self.executor.apply(self.decision(quantities=(("par:BTC", "0.4"),)))
        self.assertEqual(conflict.reason_code, "ID_CONFLICT")
        second = self.executor.apply(self.decision(quantities=(("par:BTC", "0.5"),)))
        self.assertEqual(second.sequence, conflict.sequence + 1)
        self.assertNotEqual(second.report_id, conflict.report_id)
        self.assertEqual(self.executor.reconcile("d"), completed)

    def test_unknown_blocks_same_asset_and_never_resubmits_after_restart(self):
        report = self.executor.apply(self.decision(), simulate_unknown=True)
        self.assertEqual(report.status, "unknown")
        self.assertIsNone(report.items[0].actual_quantity)
        self.journal.close()
        self.journal = Journal(self.path)
        self.executor = self.new_executor()
        self.assertEqual(self.executor.apply(self.decision()), report)
        self.assertEqual(self.executor.reconcile("d").status, "unknown")
        self.assertEqual(self.executor.apply(self.decision("other")).reason_code, "BUSY")
        self.assertEqual(self.executor.account()["positions"][0]["quantity"], "0.1")

    def test_unselected_shadow_producer_is_rejected(self):
        decision = replace(self.decision(), strategy_id="mock-agent")
        self.assertEqual(self.executor.apply(decision).reason_code, "INVALID_INPUT")

    def test_expired_future_risk_limit_and_stale_market(self):
        for decision, code in ((replace(self.decision("expired"), expires_ms=1050), "EXPIRED"),
                               (replace(self.decision("future"), created_ms=1051, expires_ms=1200), "FUTURE_SIGNAL"),
                               (self.decision("limit", (("par:BTC", "2"),)), "RISK_LIMIT")):
            self.now = 1050
            self.assertEqual(self.executor.apply(decision).reason_code, code)
        self.now = 1000
        self.market["par:ETH"]["event_ms"] = 800  # full portfolio includes untargeted holdings
        self.assertEqual(self.executor.apply(self.decision("stale")).reason_code, "DATA_UNAVAILABLE")

    def test_gate_is_deferred_for_unknown_account_or_open_orders(self):
        for invalid in (None, {**account(), "available_margin_usd": None}, {**account(), "status": "stale"}):
            verdict = self.gate.check(self.decision(), invalid, self.market, self.risk, 1000)
            self.assertEqual(verdict.status, "deferred")
        acct = account()
        acct["open_orders"] = [{"order_id": "x", "symbol": "par:BTC", "side": "buy",
                                "remaining_quantity": "0.1", "reduce_only": False}]
        verdict = self.gate.check(self.decision(), acct, self.market, self.risk, 1000)
        self.assertEqual(verdict.reason_code, "BUSY")
        self.assertEqual(RiskVerdict.from_dict(verdict.to_dict()), verdict)

    def test_risk_reduction_is_not_blocked_by_existing_limit_breach(self):
        acct = account(positions=(("par:BTC", "2"),))
        self.assertEqual(self.gate.check(self.decision(quantities=(("par:BTC", "1.5"),)),
                                         acct, self.market, self.risk, 1000).status, "approved")
        self.assertEqual(self.gate.check(self.decision(quantities=(("par:BTC", "-1.5"),)),
                                         acct, self.market, self.risk, 1000).status, "rejected")

    def test_empty_targets_do_not_flatten_positions(self):
        before = self.executor.account()["positions"]
        report = self.executor.apply(self.decision(quantities=()))
        self.assertEqual(report.status, "completed")
        self.assertEqual(self.executor.account()["positions"], before)

    def test_persisted_feedback_does_not_leak_later_reports(self):
        self.now = 1050
        self.executor.apply(self.decision())
        self.assertEqual(self.journal.reports(1049), [])
        self.assertEqual([r["status"] for r in self.journal.reports(1050)], ["accepted", "completed"])


class ResearchTests(unittest.TestCase):
    def test_immutable_reproducible_artifacts_and_manual_review(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / "research.sqlite3")
            try:
                registry = ResearchRegistry(journal)
                task = ResearchTask("task", "hypothesis", "human", 100, SYMBOLS, ("quotes",))
                candidate = StrategyCandidate("candidate", "task", "v1", 200, 100, {"rule": "demo"}, "commit", "lock")
                experiment = ExperimentRecord("experiment", "candidate", "v1", 1000, "snapshot", 900,
                                              {"train": (0, 100), "validation": (100, 200), "test": (200, 300)},
                                              "fees specified", "synthetic fills", 1, {"net_return": "0.01"}, (),
                                              "offline-command")
                for kind, artifact in (("task", task), ("candidate", candidate), ("experiment", experiment)):
                    registry.save(kind, artifact)
                    self.assertEqual(registry.get(kind, getattr(artifact, {"task": "task_id", "candidate": "candidate_id",
                                                                         "experiment": "experiment_id"}[kind])), artifact)
                self.assertEqual(registry.status("candidate"), "draft")
                approved = StrategyApproval("approval", "candidate", "v1", 1200, "approved", "reviewer",
                                            "manual evidence", "experiment")
                with self.assertRaises(ValueError):
                    registry.save("approval", approved)
                registry.save("approval", replace(approved, approval_id="evaluation", status="evaluated", created_ms=1100))
                registry.save("approval", approved)
                self.assertEqual(registry.status("candidate"), "approved")
                with self.assertRaises(ValueError):
                    replace(approved, deployment_scope="real-testnet")
                with self.assertRaises(ValueError):
                    registry.save("candidate", replace(candidate, rules_or_config={"different": True}))
                with self.assertRaises(ValueError):
                    replace(experiment, data_available_ms=1001)
                registry.save("approval", replace(approved, approval_id="retired", status="retired", created_ms=1300))
                self.assertEqual(registry.status("candidate"), "retired")
                journal.close()
                journal = Journal(Path(directory) / "research.sqlite3")
                registry = ResearchRegistry(journal)
                self.assertEqual(registry.status("candidate"), "retired")
                self.assertEqual(registry.get("candidate", "candidate"), candidate)
            finally:
                journal.close()


class DryRunTests(unittest.TestCase):
    def test_end_to_end_is_offline_simulated_and_persisted(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")), \
                    patch("perpetual_alpha.execution.Exchange", side_effect=AssertionError("Signing forbidden")):
                result = run(root / "config/dry-run.toml", path)
                again = run(root / "config/dry-run.toml", path)
            self.assertTrue(result["simulated"])
            self.assertEqual(result["execution_report"]["status"], "completed")
            self.assertEqual([t["symbol"] for t in result["decision"]["targets"]], ["par:BTC"])
            self.assertEqual(again["execution_report"]["items"][0]["filled_quantity"], "0.0000")
            journal = Journal(path)
            try:
                self.assertTrue(all(e["simulated"] and e["mode"] == "dry-run" for e in journal.events()))
                self.assertTrue({"input", "forecast", "risk-snapshot", "decision", "risk-verdict", "execution-report"}
                                <= {e["kind"] for e in journal.events()})
            finally:
                journal.close()


if __name__ == "__main__":
    unittest.main()
