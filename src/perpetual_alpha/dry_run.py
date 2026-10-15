"""Offline end-to-end composition. Fixtures only; no environment, SDK, HTTP or signing imports."""
import json
import sqlite3
import tomllib
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from .context import SOURCE, ContextBuilder
from .forecasting import (
    ForecastUnavailable,
    MockAgentForecastProducer,
    MockRiskEstimator,
    RuleForecastProducer,
    checked_forecast,
)
from .interfaces import DataRequest, decimal_string, identity, timestamp
from .journal import Journal
from .portfolio import FixedQuantityPortfolio, PortfolioUnavailable
from .producers import ProducerPlan, ProducerRegistry
from .risk import DemoRiskGate
from .simulation import MockExecution
from .storage import Store


def load_config(path):
    with open(path, "rb") as handle:
        config = tomllib.load(handle)["simulation"]
    expected = {"mode", "selected_producer", "symbols", "abstain_symbols", "asof_ms", "horizon_ms",
                "forecast_ttl_ms", "decision_ttl_ms", "max_age_ms", "policy_version", "risk_rule_version",
                "gross_limit_usd", "equity_usd", "available_margin_usd", "quantities", "quantity_limits",
                "fixture_prices"}
    optional = {"selected_producer_version", "shadow_producers", "max_inference_ms"}
    if not expected <= set(config) <= expected | optional or config["mode"] != "dry-run":
        raise ValueError("Only exact dry-run configuration is supported")
    identity(config["selected_producer"])
    if "selected_producer_version" in config:
        identity(config["selected_producer_version"])
    if not isinstance(config["symbols"], list) or not config["symbols"] or len(set(config["symbols"])) != len(config["symbols"]):
        raise ValueError("Nonempty unique symbols required")
    for key in ("quantities", "quantity_limits", "fixture_prices"):
        if set(config[key]) != set(config["symbols"]):
            raise ValueError("Every simulated asset needs explicit quantities, limits and prices")
        if any(decimal_string(v) <= 0 for v in config[key].values()):
            raise ValueError("Simulation amounts must be positive")
    timestamp(config["asof_ms"])
    return config


def built_in_registry(config, *, clock=None):
    registry = ProducerRegistry()
    registry.register(RuleForecastProducer(config["symbols"], config["horizon_ms"], config["forecast_ttl_ms"], clock=clock))
    registry.register(MockAgentForecastProducer(config["symbols"], config["horizon_ms"], config["forecast_ttl_ms"],
                                                config["abstain_symbols"], clock=clock))
    return registry


def run(config_path, journal_path, *, registry=None, clock=None, data_connection=None):
    config = load_config(config_path)
    asof_ms = config["asof_ms"]
    clock = clock or (lambda: asof_ms)  # Legacy synthetic CLI remains deterministic and entirely offline.
    registry = registry if registry is not None else built_in_registry(config, clock=clock)
    selected_producer = registry.resolve(config["selected_producer"], config.get("selected_producer_version"))
    selected_identity = (selected_producer.producer_id, selected_producer.producer_version)
    shadows = config.get("shadow_producers", [name for name in registry.identities() if name != selected_identity[0]])
    if (not isinstance(shadows, list) or any(not isinstance(name, str) for name in shadows)
            or len(set(shadows)) != len(shadows) or selected_identity[0] in shadows):
        raise ValueError("shadow_producers must be unique registered IDs excluding the controller")
    producers = [selected_producer, *(registry.resolve(name) for name in shadows)]
    registered_identities = {id(p): (p.producer_id, p.producer_version) for p in producers}
    # Keep fixture quote storage separate from both the real collector and durable simulator journal.
    store = Store(":memory:") if data_connection is None else None
    journal = Journal(journal_path)
    try:
        if store is not None:
            for s in config["symbols"]:
                price = decimal_string(config["fixture_prices"][s])
                store.save({"symbol": s, "event_ms": asof_ms - 1000, "received_ms": asof_ms - 500,
                            "context_received_ms": asof_ms - 600, "bid": str(price - Decimal("1")),
                            "ask": str(price + Decimal("1")), "mark": str(price),
                            "oracle": str(price - Decimal("2")), "funding": "0", "raw": {},
                            "source": SOURCE, "schema_version": 1})
        try:
            plan = ProducerPlan(producers)
        except ValueError as error:
            journal.append("producer-configuration-error", {"reason": str(error)}, clock())
            raise
        builder = ContextBuilder(store.connection if store is not None else data_connection)
        context_id = "dry-run:" + uuid4().hex
        risk = None
        # Execution's independent price needs are not inferred from any producer's requests.
        market_requests = tuple(DataRequest("execution:" + s, SOURCE, "quote", (s,), ("bid", "ask", "mark"),
                                            0, 1, config["max_age_ms"]) for s in config["symbols"])

        def latest_market():
            fresh = builder.build(market_requests, context_id + ":execution", clock())
            result = {}
            for dataset in fresh["datasets"].values():
                if dataset["status"] == "ready":
                    row = dataset["records"][-1]
                    result[row["symbol"]] = {**row["data"], "observed_ms": row["received_ms"],
                                              "event_ms": row["event_ms"]}
            return result

        gate = DemoRiskGate(config["quantity_limits"], config["gross_limit_usd"], config["max_age_ms"],
                            config["risk_rule_version"])
        executor = MockExecution(journal, gate, latest_market, lambda: risk, clock, selected_identity)
        executor.initialize_account({"observed_ms": asof_ms, "status": "ready", "equity_usd": config["equity_usd"],
                                     "available_margin_usd": config["available_margin_usd"],
                                     "positions": [], "open_orders": []})
        shared = builder.build(plan.requests, context_id, asof_ms, executor.account(), journal.reports(asof_ms))
        journal.append("input", {"fixture": data_connection is None, "context": shared, "config": config}, clock())
        batches, failures, contexts = [], {}, {}

        def unavailable(producer, failure):
            name, version = registered_identities[id(producer)]
            payload = {"producer_id": name, "producer_version": version,
                       "role": "selected" if producer is selected_producer else "shadow", **failure}
            failures[name] = payload
            journal.append("producer-unavailable", payload, clock())

        def predict(producer):
            name, version = registered_identities[id(producer)]
            if name in plan.failures:
                unavailable(producer, plan.failures[name])
                return None
            view = plan.context_for(name, shared)
            contexts[name] = view
            journal.append("producer-input", {"producer_id": name, "producer_version": producer.producer_version,
                                               "context": view}, clock())
            try:
                registry.resolve(name, version)
                batch = checked_forecast(producer, view, clock=clock, requirements=plan.requirements_for(name),
                                         max_inference_ms=config.get("max_inference_ms", config["max_age_ms"]),
                                         max_input_age_ms=config["max_age_ms"], raise_on_error=True)
                registry.resolve(name, batch.producer_version)
                batches.append(batch)
                journal.append("forecast", batch.to_dict(), clock())
                if any(item.status == "error" for item in batch.items):
                    raise ForecastUnavailable("PRODUCER_ERROR", "Producer returned explicit error forecast")
                return batch
            except (ForecastUnavailable, ValueError) as error:
                failure = error.to_dict() if isinstance(error, ForecastUnavailable) else {
                    "reason_code": "INVALID_PRODUCER", "reason": str(error)}
                unavailable(producer, failure)
                return None

        # Consume the controller immediately. Slow/failing shadows cannot age out its decision.
        selected = predict(selected_producer)
        result = {"mode": "dry-run", "simulated": True, "fixture": data_connection is None,
                  "context": contexts.get(selected_identity[0], shared), "selected_producer": selected_identity[0],
                  "journal": str(Path(journal_path).resolve())}
        if selected is not None:
            risk = MockRiskEstimator(config["symbols"], config["horizon_ms"]).estimate(result["context"])
            journal.append("risk-snapshot", risk.to_dict(), clock())
            result["risk_snapshot"] = risk.to_dict()
            policy = FixedQuantityPortfolio(config["quantities"], config["horizon_ms"], config["max_age_ms"],
                                            config["decision_ttl_ms"], config["policy_version"])
            try:
                decision = policy.construct(selected, risk, result["context"], clock(), context_id + ":decision")
                report = executor.apply(decision)
                verdicts = [e["payload"] for e in journal.events()
                            if e["kind"] == "risk-verdict" and e["payload"]["decision_id"] == decision.decision_id]
                result.update(decision=decision.to_dict(), execution_report=report.to_dict(),
                              risk_verdict=verdicts[-1] if verdicts else None)
            except PortfolioUnavailable as error:
                journal.append("portfolio-unavailable", {"reason": str(error)}, clock())
                unavailable(selected_producer, {"reason_code": "PORTFOLIO_UNAVAILABLE", "reason": str(error)})
        for producer in producers[1:]:
            predict(producer)
        result.update(forecasts=[b.to_dict() for b in batches], failures=failures, producer_contexts=contexts)
        if "execution_report" not in result:
            result.update(status="blocked", reason=failures[selected_identity[0]]["reason"])
        return result
    finally:
        journal.close()
        if store is not None:
            store.close()


def open_collector_readonly(path):
    """Optional offline adapter entry point; cannot initialize/migrate the collector database."""
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def print_run(config_path, journal_path):
    result = run(config_path, journal_path)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result.get("execution_report", {}).get("status") == "completed" else 1
