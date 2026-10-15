"""P0 regressions written against main c6ca2ab before the stabilization implementation."""
import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import get_type_hints
from unittest.mock import patch

from perpetual_alpha.context import SOURCE, ContextBuilder
from perpetual_alpha.contracts import AgentAdapter, Forecast, ForecastBatch, ForecastProducer
from perpetual_alpha.dry_run import load_config, run
from perpetual_alpha.forecasting import checked_forecast
from perpetual_alpha.interfaces import DataRequest
from perpetual_alpha.journal import Journal
from perpetual_alpha.storage import Store

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, now=100000):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, delta=8000):
        self.now += delta


class IndependentProducer:
    producer_id = "independent-lt"
    producer_version = "test-v1"

    def __init__(self, clock, *, delay=8000, funding=True):
        self.clock, self.delay, self.funding = clock, delay, funding
        self.seen = []

    def requirements(self):
        requests = (DataRequest("lt_btc", SOURCE, "quote", ("par:BTC",), ("bid", "ask"), 0, 1, 60000),)
        if self.funding:
            requests += (DataRequest("lt_eth", SOURCE, "quote", ("par:ETH",), ("funding",), 0, 1, 60000),)
        return requests

    def forecast(self, context):
        self.seen.append(copy.deepcopy(context))
        self.clock.advance(self.delay)
        return ForecastBatch("batch:" + context["context_id"] + ":" + self.producer_id, context["context_id"],
                             self.producer_id, self.producer_version, self.clock(), tuple(
                                 Forecast(s, context["asof_ms"], context["asof_ms"] + 60000, 3600000,
                                          "ready", "up", (r.dataset_id,))
                                 for r in self.requirements() for s in r.symbols))


class StabilizationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.producer = IndependentProducer(self.clock)
        self.store = Store(":memory:")
        for symbol in ("par:BTC", "par:ETH"):
            self.store.save({"symbol": symbol, "event_ms": 99000, "received_ms": 99500,
                             "bid": "99", "ask": "101", "mark": "100", "oracle": "99", "funding": "0",
                             "context_received_ms": 99400, "source": SOURCE, "schema_version": 1})
        self.context = ContextBuilder(self.store.connection).build(self.producer.requirements(), "ctx", 100000)

    def tearDown(self):
        self.store.close()

    def checked(self, producer=None, **kwargs):
        return checked_forecast(producer or self.producer, self.context, clock=self.clock,
                                max_inference_ms=10000, max_input_age_ms=60000, **kwargs)

    def test_t01_delayed_completion_uses_frozen_asof_and_actual_created_time(self):
        batch = self.checked()
        self.assertEqual(batch.created_ms, 108000)
        self.assertTrue(all(i.status == "ready" and i.asof_ms == 100000 for i in batch.items))

    def test_t02_invalid_timestamps_id_evidence_and_deadlines_fail_closed(self):
        from perpetual_alpha.forecasting import ForecastUnavailable

        for mutation in (
            lambda b: replace(b, created_ms=99000),
            lambda b: replace(b, created_ms=109000),
            lambda b: replace(b, context_id="wrong-context"),
            lambda b: replace(b, producer_version="wrong-version"),
            lambda b: replace(b, items=(replace(b.items[0], asof_ms=101000),)),
            lambda b: replace(b, items=(replace(b.items[0], asof_ms=99000),)),
            lambda b: replace(b, items=(replace(b.items[0], expires_ms=108001),)),
        ):
            with self.subTest(mutation=mutation):
                self.clock.now = 100000
                def forecast(context):
                    result = IndependentProducer.forecast(self.producer, context)
                    result = mutation(result)
                    self.clock.advance(2)
                    return result
                with patch.object(self.producer, "forecast", side_effect=forecast), self.assertRaises(ForecastUnavailable):
                    self.checked()
        self.clock.now = 100000
        self.producer.delay = 11000
        with self.assertRaises(ForecastUnavailable) as failed:
            self.checked()
        self.assertEqual(failed.exception.reason_code, "INFERENCE_TIMEOUT")

    def test_t02_stale_input_rejected_before_calling_producer(self):
        from perpetual_alpha.forecasting import ForecastUnavailable

        self.clock.now = 161000
        with self.assertRaises(ForecastUnavailable):
            self.checked()
        self.assertEqual(self.producer.seen, [])

    def test_t02_context_source_account_feedback_and_complete_fields_are_verified(self):
        from perpetual_alpha.forecasting import ForecastUnavailable

        original = copy.deepcopy(self.context)
        for mutate in (
            lambda c: c["datasets"]["lt_btc"].update(source="wrong-source"),
            lambda c: c["datasets"]["lt_btc"]["records"][0]["data"].pop("ask"),
            lambda c: c.update(account={"observed_ms": 100001}),
            lambda c: c.update(execution_reports=[{"observed_ms": 100001}]),
        ):
            with self.subTest(mutate=mutate):
                self.context = copy.deepcopy(original)
                mutate(self.context)
                with self.assertRaises(ForecastUnavailable):
                    self.checked()

    def test_t02_undeclared_assets_evidence_and_adapter_errors_remain_unavailable(self):
        from perpetual_alpha.forecasting import ForecastUnavailable

        for failure in (TimeoutError("timeout"), RuntimeError("failed")):
            with patch.object(self.producer, "forecast", side_effect=failure), self.assertRaises(ForecastUnavailable):
                self.checked()
        for changes in ({"symbol": "par:GOLD"}, {"evidence_refs": ("unregistered",)}):
            self.clock.now = 100000
            def invalid(context):
                batch = IndependentProducer.forecast(self.producer, context)
                return replace(batch, items=(replace(batch.items[0], **changes),))
            with patch.object(self.producer, "forecast", side_effect=invalid), self.assertRaises(ForecastUnavailable):
                self.checked()

    def test_t02_context_too_old_at_receive_even_within_inference_budget(self):
        from perpetual_alpha.forecasting import ForecastUnavailable

        with self.assertRaises(ForecastUnavailable) as failed:
            checked_forecast(self.producer, self.context, clock=self.clock,
                             max_inference_ms=10000, max_input_age_ms=5000)
        self.assertEqual(failed.exception.reason_code, "STALE_INPUT")

    def test_t02_requirements_failure_is_not_retried_in_error_handler(self):
        from perpetual_alpha.forecasting import ForecastUnavailable

        with patch.object(self.producer, "requirements", side_effect=RuntimeError("broken")) as requirements:
            with self.assertRaises(ForecastUnavailable) as failed:
                self.checked()
            self.assertEqual(failed.exception.reason_code, "INVALID_PRODUCER")
            self.assertEqual(requirements.call_count, 1)

    def test_t03_agent_adapter_matches_standard_interface(self):
        self.assertEqual(get_type_hints(AgentAdapter), get_type_hints(ForecastProducer))
        self.assertTrue(callable(getattr(AgentAdapter, "requirements", None)))

    def test_t03_missing_requirements_fails_at_registration(self):
        from perpetual_alpha.producers import ProducerRegistry

        class ForecastOnly:
            producer_id, producer_version = "incomplete", "v1"
            def forecast(self, context):
                raise AssertionError("Must not be called")
        with self.assertRaisesRegex(ValueError, "requirements"):
            ProducerRegistry().register(ForecastOnly())

    def test_t05_registry_rejects_unregistered_duplicate_and_version_conflicts(self):
        from perpetual_alpha.producers import ProducerRegistry

        registry = ProducerRegistry()
        registry.register(self.producer)
        self.assertIs(registry.resolve("independent-lt", "test-v1"), self.producer)
        for name, version in (("unregistered", "test-v1"), ("independent-lt", "wrong")):
            with self.assertRaises(ValueError):
                registry.resolve(name, version)
        with self.assertRaises(ValueError):
            registry.register(self.producer)
        other = IndependentProducer(self.clock)
        other.producer_version = "other-v2"
        with self.assertRaises(ValueError):
            registry.register(other)
        self.producer.producer_version = "changed-after-registration"
        with self.assertRaises(ValueError):
            registry.resolve("independent-lt")

    def test_t06_requests_are_collected_for_all_producers_and_scoped(self):
        from perpetual_alpha.producers import ProducerPlan

        btc = IndependentProducer(self.clock, delay=0, funding=False)
        btc.producer_id = "btc-control"
        plan = ProducerPlan((btc, self.producer))
        self.assertEqual({r.dataset_id for r in plan.requests}, {"lt_btc", "lt_eth"})
        context = ContextBuilder(self.store.connection).build(plan.requests, "ctx", 100000)
        self.assertEqual(set(plan.context_for("btc-control", context)["datasets"]), {"lt_btc"})
        self.assertEqual(set(plan.context_for("independent-lt", context)["datasets"]), {"lt_btc", "lt_eth"})
        self.assertEqual(plan.context_for("btc-control", context)["context_id"], "ctx")

    def test_t07_request_conflict_cannot_silently_override(self):
        from perpetual_alpha.producers import ProducerPlan

        other = IndependentProducer(self.clock)
        other.producer_id = "other"
        original = other.requirements()
        for field in ({"source": "other-source"}, {"kind": "candle"}, {"symbols": ("par:ETH",)},
                      {"fields": ("mark",)}, {"lookback_ms": 1}, {"max_records": 2}, {"max_age_ms": 1},
                      {"required": False}, {"parameters": {"interval": "1h"}}):
            with self.subTest(field=field), patch.object(other, "requirements", return_value=(replace(original[0], **field),)):
                with self.assertRaisesRegex(ValueError, "dataset_id"):
                    ProducerPlan((self.producer, other))

    def test_t06_producer_mutation_does_not_change_shared_context(self):
        original = copy.deepcopy(self.context)
        actual = self.producer.forecast
        def mutating(context):
            result = actual(context)
            context["datasets"].clear()
            return result
        with patch.object(self.producer, "forecast", side_effect=mutating):
            self.checked()
        self.assertEqual(self.context, original)


class RunnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.config = load_config(ROOT / "config/dry-run.toml")
        self.clock = Clock(self.config["asof_ms"])
        self.run_count = 0

    def tearDown(self):
        self.directory.cleanup()

    def run_custom(self, *, selected="independent-lt", missing_eth=False, unsupported=False,
                   delay=8000, modify_batch=None, broken_requirements=False):
        from perpetual_alpha.producers import ProducerRegistry

        config = self.config
        self.clock.now = config["asof_ms"]
        self.run_count += 1
        self.journal_path = self.path / f"journal-{self.run_count}.sqlite3"
        config.update(selected_producer=selected, selected_producer_version="test-v1",
                      shadow_producers=["btc-only" if selected == "independent-lt" else "independent-lt"],
                      max_inference_ms=10000)
        dummy = IndependentProducer(self.clock, delay=delay)
        btc = IndependentProducer(self.clock, delay=0, funding=False)
        btc.producer_id = "btc-only"
        if unsupported:
            original = dummy.requirements()
            dummy.requirements = lambda: (original[0], replace(original[1], kind="news"))
        if modify_batch is not None:
            forecast = dummy.forecast
            dummy.forecast = lambda context: modify_batch(forecast(context))
        if broken_requirements:
            dummy.requirements = unittest.mock.Mock(side_effect=RuntimeError("broken requirements"))
        registry = ProducerRegistry()
        registry.register(btc)
        registry.register(dummy)
        store = Store(":memory:")
        try:
            for s in (("par:BTC",) if missing_eth else ("par:BTC", "par:ETH")):
                now = config["asof_ms"]
                store.save({"symbol": s, "event_ms": now - 1000, "received_ms": now - 500,
                            "bid": "99", "ask": "101", "mark": "100", "oracle": "99", "funding": "0",
                            "context_received_ms": now - 600, "source": SOURCE, "schema_version": 1})
            with patch("perpetual_alpha.dry_run.load_config", return_value=config), \
                    patch("urllib.request.urlopen", side_effect=AssertionError("No network")), \
                    patch("perpetual_alpha.execution.Exchange", side_effect=AssertionError("No signing")):
                result = run("unused", self.journal_path, registry=registry,
                             clock=self.clock, data_connection=store.connection)
        finally:
            store.close()
        return result, dummy, btc

    def test_t03_t11_custom_selected_producer_completes_multi_asset_pipeline(self):
        result, dummy, btc = self.run_custom()
        self.assertEqual(result["execution_report"]["status"], "completed")
        self.assertEqual(result["decision"]["strategy_version"], "test-v1")
        self.assertEqual(result["decision"]["created_ms"], self.config["asof_ms"] + 8000)
        self.assertEqual({t["symbol"] for t in result["decision"]["targets"]}, {"par:BTC", "par:ETH"})
        self.assertEqual(dummy.seen[0]["asof_ms"], btc.seen[0]["asof_ms"])
        self.assertTrue(result["simulated"])

    def test_t04_t08_missing_shadow_data_does_not_block_btc_controller(self):
        result, _, _ = self.run_custom(selected="btc-only", missing_eth=True)
        self.assertEqual(result["execution_report"]["status"], "completed")
        self.assertEqual([t["symbol"] for t in result["decision"]["targets"]], ["par:BTC"])
        journal = Journal(self.journal_path)
        try:
            events = journal.events()
            self.assertTrue(any(e["kind"] == "producer-unavailable" and
                                e["payload"]["producer_id"] == "independent-lt" for e in events))
            self.assertEqual({e["payload"]["strategy_id"] for e in events if e["kind"] == "decision"}, {"btc-only"})
        finally:
            journal.close()

    def test_t04_valid_shadow_is_recorded_without_controlling_positions(self):
        result, _, _ = self.run_custom(selected="btc-only")
        self.assertEqual({b["producer_id"] for b in result["forecasts"]}, {"btc-only", "independent-lt"})
        self.assertEqual([t["symbol"] for t in result["decision"]["targets"]], ["par:BTC"])
        self.assertEqual(result["decision"]["created_ms"], self.config["asof_ms"])

    def test_t04_shadow_timeout_cannot_age_out_the_controller(self):
        result, _, _ = self.run_custom(selected="btc-only", delay=100000)
        self.assertEqual(result["execution_report"]["status"], "completed")
        self.assertEqual(result["failures"]["independent-lt"]["reason_code"], "INFERENCE_TIMEOUT")

    def test_t08_shadow_unsupported_source_and_failed_declaration_are_isolated(self):
        result, _, _ = self.run_custom(selected="btc-only", unsupported=True)
        self.assertEqual(result["execution_report"]["status"], "completed")
        result, dummy, _ = self.run_custom(selected="btc-only", broken_requirements=True)
        self.assertEqual(result["execution_report"]["status"], "completed")
        self.assertEqual(result["failures"]["independent-lt"]["reason_code"], "INVALID_PRODUCER")
        self.assertEqual(dummy.requirements.call_count, 1)

    def test_t09_selected_missing_or_unsupported_data_never_executes(self):
        for kwargs in ({"missing_eth": True}, {"unsupported": True}):
            with self.subTest(kwargs=kwargs):
                result, _, _ = self.run_custom(**kwargs)
                self.assertEqual(result["status"], "blocked")
                self.assertNotIn("execution_report", result)
                journal = Journal(self.journal_path)
                try:
                    self.assertFalse(any(e["kind"] == "decision" for e in journal.events()))
                finally:
                    journal.close()

    def test_t02_selected_timeout_does_not_create_any_decision(self):
        result, _, _ = self.run_custom(delay=11000)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["failures"]["independent-lt"]["reason_code"], "INFERENCE_TIMEOUT")

    def test_t10_neutral_abstain_error_and_unpredicted_assets_keep_existing_semantics(self):
        result, _, _ = self.run_custom(modify_batch=lambda b: replace(b, items=(
            replace(b.items[0], direction="neutral"), replace(b.items[1], status="abstain", direction=None))))
        self.assertEqual(result["decision"]["targets"], [])
        result, _, _ = self.run_custom(modify_batch=lambda b: replace(b, items=(b.items[0],)))
        self.assertEqual([t["symbol"] for t in result["decision"]["targets"]], ["par:BTC"])
        result, _, _ = self.run_custom(modify_batch=lambda b: replace(b, items=(
            replace(b.items[0], status="error", direction=None),)))
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("decision", result)

    def test_t12_copyable_dummy_example_runs_without_any_real_model(self):
        from perpetual_alpha.dry_run import built_in_registry
        from perpetual_alpha.examples.lt_adapter import DummyLTProducer

        config_path = ROOT / "config/dry-run-lt.toml"
        config = load_config(config_path)
        registry = built_in_registry(config, clock=self.clock)
        registry.register(DummyLTProducer(self.clock, lambda: self.clock.advance(8000)))
        with patch("urllib.request.urlopen", side_effect=AssertionError("No network")), \
                patch("perpetual_alpha.execution.Exchange", side_effect=AssertionError("No signing")):
            result = run(config_path, self.path / "dummy.sqlite3", registry=registry, clock=self.clock)
        self.assertEqual(result["execution_report"]["status"], "completed")
        self.assertEqual(result["decision"]["strategy_version"], "offline-0.1")
        self.assertEqual(result["decision"]["created_ms"], config["asof_ms"] + 8000)
        self.assertEqual(result["execution_report"]["protocol_version"], "1.0")
        self.assertTrue(all(b["schema_version"] == "forecast/0.1" for b in result["forecasts"]))
        self.assertTrue(all(b["created_ms"] == config["asof_ms"] + 8000 for b in result["forecasts"]))


if __name__ == "__main__":
    unittest.main()
