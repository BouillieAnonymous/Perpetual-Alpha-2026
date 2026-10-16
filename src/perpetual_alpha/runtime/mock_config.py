"""Explicit synthetic service configuration. No env/.env, signing settings or live backend."""
import tomllib

from ..interfaces import decimal_string, identity
from ..journal import canonical
from ..portfolio import FixedQuantityPortfolio
from ..risk import DemoRiskGate
from .contracts import decode

SERVICE_FIELDS = {"mode", "schedule_id", "producer_id", "producer_version", "control_scope", "control_epoch",
                  "interval_ms", "deadline_ms", "lease_ms", "heartbeat_ms", "poll_ms", "max_queries", "max_records",
                  "max_bytes", "max_attempts", "max_input_age_ms", "max_inference_ms", "http_timeout_ms",
                  "max_http_body", "max_http_workers"}
SIMULATION_FIELDS = {"configuration_id", "horizon_ms", "forecast_ttl_ms", "decision_ttl_ms", "max_age_ms",
                     "policy_version", "risk_rule_version", "gross_limit_usd", "equity_usd", "available_margin_usd",
                     "quantities", "quantity_limits", "fixture_prices"}


def load_config(path):
    with open(path, "rb") as stream:
        config = tomllib.load(stream)
    if set(config) != {"service", "simulation"}:
        raise ValueError("Exact mock service configuration required")
    service, simulation = config["service"], config["simulation"]
    if set(service) != SERVICE_FIELDS or set(simulation) != SIMULATION_FIELDS or service["mode"] != "mock-http":
        raise ValueError("Only explicit mock-http configuration supported")
    for name, value in service.items():
        if name.endswith("_id") or name.endswith("_version") or name in {"mode", "control_scope"}:
            decode(str, value)
        else:
            decode(int, value)
            if value < 1:
                raise ValueError("Positive service budgets required")
    if service["heartbeat_ms"] * 2 >= service["lease_ms"] or service["max_http_workers"] > 64:
        raise ValueError("Heartbeat safety margin or worker limit invalid")
    if service["max_http_body"] > 4 * 1024 * 1024 or service["http_timeout_ms"] > 60000:
        raise ValueError("Bounded HTTP configuration required")
    identity(simulation["configuration_id"])
    symbols = set(simulation["quantities"])
    if not symbols or symbols != set(simulation["quantity_limits"]) or symbols != set(simulation["fixture_prices"]):
        raise ValueError("All synthetic assets require quantities, limits and fixture prices")
    if any(decimal_string(v) <= 0 for v in simulation["fixture_prices"].values()):
        raise ValueError("Positive fixture prices required")
    policy(simulation)
    gate(simulation)
    if decimal_string(simulation["equity_usd"]) <= 0 or decimal_string(simulation["available_margin_usd"]) < 0:
        raise ValueError("Synthetic account amounts invalid")
    decode(int, simulation["forecast_ttl_ms"])
    if simulation["forecast_ttl_ms"] < 1:
        raise ValueError("Positive forecast TTL required")
    canonical(config)
    return config


def policy(config):
    return FixedQuantityPortfolio(config["quantities"], config["horizon_ms"], config["max_age_ms"],
                                  config["decision_ttl_ms"], config["policy_version"])


def gate(config):
    return DemoRiskGate(config["quantity_limits"], config["gross_limit_usd"], config["max_age_ms"],
                        config["risk_rule_version"])
