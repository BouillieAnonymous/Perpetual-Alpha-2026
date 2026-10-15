"""Offline end-to-end composition. Fixtures only; no environment, SDK, HTTP or signing imports."""
import json
import sqlite3
import tomllib
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from .context import SOURCE, ContextBuilder
from .forecasting import MockAgentForecastProducer, MockRiskEstimator, RuleForecastProducer, checked_forecast
from .interfaces import decimal_string, timestamp
from .journal import Journal
from .portfolio import FixedQuantityPortfolio, PortfolioUnavailable
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
    if set(config) != expected or config["mode"] != "dry-run":
        raise ValueError("Only exact dry-run configuration is supported")
    if config["selected_producer"] not in {"rule-demo", "mock-agent"}:
        raise ValueError("Unknown forecast producer")
    if not isinstance(config["symbols"], list) or not config["symbols"] or len(set(config["symbols"])) != len(config["symbols"]):
        raise ValueError("Nonempty unique symbols required")
    for key in ("quantities", "quantity_limits", "fixture_prices"):
        if set(config[key]) != set(config["symbols"]):
            raise ValueError("Every simulated asset needs explicit quantities, limits and prices")
        if any(decimal_string(v) <= 0 for v in config[key].values()):
            raise ValueError("Simulation amounts must be positive")
    timestamp(config["asof_ms"])
    return config


def run(config_path, journal_path):
    config = load_config(config_path)
    now = config["asof_ms"]
    # Keep fixture quote storage separate from both the real collector and durable simulator journal.
    store = Store(":memory:")
    journal = Journal(journal_path)
    try:
        for s in config["symbols"]:
            price = decimal_string(config["fixture_prices"][s])
            store.save({"symbol": s, "event_ms": now - 1000, "received_ms": now - 500,
                        "context_received_ms": now - 600, "bid": str(price - Decimal("1")),
                        "ask": str(price + Decimal("1")), "mark": str(price),
                        "oracle": str(price - Decimal("2")), "funding": "0", "raw": {},
                        "source": SOURCE, "schema_version": 1})
        producers = [RuleForecastProducer(config["symbols"], config["horizon_ms"], config["forecast_ttl_ms"]),
                     MockAgentForecastProducer(config["symbols"], config["horizon_ms"], config["forecast_ttl_ms"],
                                               config["abstain_symbols"])]
        requests = producers[0].requirements()
        builder = ContextBuilder(store.connection)
        context_id = "dry-run:" + uuid4().hex
        market_context = builder.build(requests, context_id, now)
        risk = MockRiskEstimator(config["symbols"], config["horizon_ms"]).estimate(market_context)

        def latest_market():
            fresh = builder.build(requests, context_id + ":execution", now)
            result = {}
            for dataset in fresh["datasets"].values():
                if dataset["status"] == "ready":
                    row = dataset["records"][-1]
                    result[row["symbol"]] = {**row["data"], "observed_ms": row["received_ms"],
                                              "event_ms": row["event_ms"]}
            return result

        gate = DemoRiskGate(config["quantity_limits"], config["gross_limit_usd"], config["max_age_ms"],
                            config["risk_rule_version"])
        executor = MockExecution(journal, gate, latest_market, lambda: risk, lambda: now,
                                 (config["selected_producer"], "0.1"))
        executor.initialize_account({"observed_ms": now, "status": "ready", "equity_usd": config["equity_usd"],
                                     "available_margin_usd": config["available_margin_usd"],
                                     "positions": [], "open_orders": []})
        context = builder.build(requests, context_id, now, executor.account(), journal.reports(now))
        journal.append("input", {"fixture": True, "context": context, "config": config}, now)
        batches = [checked_forecast(producer, context) for producer in producers]
        for batch in batches:
            journal.append("forecast", batch.to_dict(), now)
        journal.append("risk-snapshot", risk.to_dict(), now)
        selected = next(b for b in batches if b.producer_id == config["selected_producer"])
        policy = FixedQuantityPortfolio(config["quantities"], config["horizon_ms"], config["max_age_ms"],
                                        config["decision_ttl_ms"], config["policy_version"])
        try:
            decision = policy.construct(selected, risk, context, now, context_id + ":decision")
        except PortfolioUnavailable as error:
            journal.append("portfolio-unavailable", {"reason": str(error)}, now)
            return {"mode": "dry-run", "simulated": True, "status": "blocked", "reason": str(error)}
        report = executor.apply(decision)
        verdicts = [e["payload"] for e in journal.events()
                    if e["kind"] == "risk-verdict" and e["payload"]["decision_id"] == decision.decision_id]
        return {"mode": "dry-run", "simulated": True, "fixture": True,
                "context": context, "forecasts": [b.to_dict() for b in batches],
                "selected_producer": selected.producer_id, "risk_snapshot": risk.to_dict(),
                "decision": decision.to_dict(), "risk_verdict": verdicts[-1] if verdicts else None,
                "execution_report": report.to_dict(), "journal": str(Path(journal_path).resolve())}
    finally:
        journal.close()
        store.close()


def open_collector_readonly(path):
    """Optional offline adapter entry point; cannot initialize/migrate the collector database."""
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def print_run(config_path, journal_path):
    result = run(config_path, journal_path)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result.get("execution_report", {}).get("status") == "completed" else 1
