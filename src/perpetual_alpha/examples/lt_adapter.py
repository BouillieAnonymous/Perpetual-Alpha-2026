"""Copyable ForecastProducer adapter and explicit registration. No LLM, network, or trading API."""
import argparse
import json

from perpetual_alpha.context import SOURCE
from perpetual_alpha.contracts import Forecast, ForecastBatch
from perpetual_alpha.dry_run import built_in_registry, load_config, run
from perpetual_alpha.interfaces import DataRequest, timestamp


class SimulationClock:
    def __init__(self, asof_ms):
        timestamp(asof_ms)
        self.now_ms = asof_ms

    def __call__(self):
        return self.now_ms

    def advance(self, delta_ms):
        timestamp(delta_ms)
        self.now_ms += delta_ms


class DummyLTProducer:
    """Standalone harness adapter: replace only the local dummy inference with LT's own harness.

    finish_inference advances a test clock; it performs no real sleep or external model call.
    Production adapters must put a finite timeout on every blocking call in their harness.
    """
    producer_id = "dummy-lt"
    producer_version = "offline-0.1"

    def __init__(self, clock, finish_inference=None, horizon_ms=3600000, ttl_ms=60000):
        self.clock = clock
        self.finish_inference = finish_inference or (lambda: None)
        self.horizon_ms, self.ttl_ms = horizon_ms, ttl_ms
        self._requests = (
            DataRequest("lt_btc_quotes", SOURCE, "quote", ("par:BTC",), ("bid", "ask"), 0, 1, ttl_ms),
            DataRequest("lt_eth_funding", SOURCE, "quote", ("par:ETH",), ("funding",), 0, 1, ttl_ms),
        )

    def requirements(self) -> tuple[DataRequest, ...]:
        return self._requests

    def forecast(self, context: dict) -> ForecastBatch:
        # Here a real adapter would call its read-only harness using ONLY the provided context.
        # Constant directions below are deliberately synthetic; no probability is invented.
        self.finish_inference()
        asof_ms = context["asof_ms"]
        items = []
        for request in self._requests:
            available = context["datasets"][request.dataset_id]["status"] == "ready"
            items.append(Forecast(request.symbols[0], asof_ms, asof_ms + self.ttl_ms, self.horizon_ms,
                                  "ready" if available else "abstain", "up" if available else None,
                                  (request.dataset_id,) if available else (), reason="OFFLINE DUMMY ONLY"))
        return ForecastBatch(context["context_id"] + ":dummy-lt", context["context_id"],
                             self.producer_id, self.producer_version, self.clock(), tuple(items))


def main():
    parser = argparse.ArgumentParser(description="Offline LT adapter integration example")
    parser.add_argument("--config", default="config/dry-run-lt.toml")
    parser.add_argument("--journal", default="data/dry-run/lt-journal.sqlite3")
    args = parser.parse_args()
    config = load_config(args.config)
    clock = SimulationClock(config["asof_ms"])
    registry = built_in_registry(config, clock=clock)
    registry.register(DummyLTProducer(clock, lambda: clock.advance(8000),
                                      config["horizon_ms"], config["forecast_ttl_ms"]))
    result = run(args.config, args.journal, registry=registry, clock=clock)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("execution_report", {}).get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
