"""Deterministic demonstration producers, comparable on the same frozen context."""
from decimal import Decimal

from .context import SOURCE
from .contracts import Forecast, ForecastBatch, RiskSnapshot, positive_ms
from .interfaces import DataRequest, identity


class RuleForecastProducer:
    producer_id = "rule-demo"
    producer_version = "0.1"

    def __init__(self, symbols, horizon_ms, ttl_ms):
        self.symbols = tuple(symbols)
        positive_ms(horizon_ms)
        positive_ms(ttl_ms)
        self.horizon_ms, self.ttl_ms = horizon_ms, ttl_ms

    def requirements(self):
        return tuple(DataRequest(s + "_quotes", SOURCE, "quote", (s,), ("bid", "ask", "mark", "oracle"),
                                 0, 1, self.ttl_ms) for s in self.symbols)

    def forecast(self, context):
        items = []
        now = context["asof_ms"]
        for s in self.symbols:
            dataset_id = s + "_quotes"
            dataset = context["datasets"].get(dataset_id)
            direction, status, refs = None, "abstain", ()
            reason = "Missing or unavailable quote"
            if dataset and dataset["status"] == "ready":
                record = dataset["records"][-1]
                mark, oracle = Decimal(record["data"]["mark"]), Decimal(record["data"]["oracle"])
                direction = "up" if mark > oracle else "down" if mark < oracle else "neutral"
                status, refs, reason = "ready", (dataset_id,), "DEMO ONLY: mark versus oracle"
            items.append(Forecast(s, now, now + self.ttl_ms, self.horizon_ms, status, direction, refs, reason))
        return ForecastBatch(context["context_id"] + ":" + self.producer_id, context["context_id"],
                             self.producer_id, self.producer_version, now, tuple(items))


class MockAgentForecastProducer(RuleForecastProducer):
    producer_id = "mock-agent"

    def __init__(self, symbols, horizon_ms, ttl_ms, abstain_symbols):
        super().__init__(symbols, horizon_ms, ttl_ms)
        self.abstain_symbols = set(abstain_symbols)
        if not self.abstain_symbols <= set(self.symbols):
            raise ValueError("Unknown abstention symbol")

    def forecast(self, context):
        baseline = super().forecast(context)
        items = tuple(Forecast(i.symbol, i.asof_ms, i.expires_ms, i.horizon_ms, "abstain", None,
                               (), "MOCK AGENT: configured abstention") if i.symbol in self.abstain_symbols
                      else i for i in baseline.items)
        return ForecastBatch(baseline.forecast_batch_id, baseline.context_id, self.producer_id,
                             self.producer_version, baseline.created_ms, items)


def checked_forecast(producer, context):
    """Invalid/failed adapters cannot masquerade as ready forecasts. Real timeouts remain reserved."""
    try:
        batch = producer.forecast(context)
        # Revalidate the JSON boundary, including mutations inside frozen objects.
        batch = ForecastBatch.from_dict(batch.to_dict())
        if (batch.context_id != context["context_id"] or batch.created_ms != context["asof_ms"]
                or batch.producer_id != producer.producer_id or batch.producer_version != producer.producer_version):
            raise ValueError("Producer/context identity mismatch")
        allowed = {s for r in producer.requirements() for s in r.symbols}
        if any(i.symbol not in allowed for i in batch.items):
            raise ValueError("Undeclared forecast asset")
        for item in batch.items:
            for ref in item.evidence_refs:
                if ref not in context["datasets"] or context["datasets"][ref]["status"] != "ready":
                    raise ValueError("Unknown or unavailable evidence reference")
        return batch
    except Exception as error:
        identity(producer.producer_id, producer.producer_version)
        now = context["asof_ms"]
        symbols = sorted({s for r in producer.requirements() for s in r.symbols})
        return ForecastBatch(context["context_id"] + ":" + producer.producer_id, context["context_id"],
                             producer.producer_id, producer.producer_version, now,
                             tuple(Forecast(s, now, now + 1, 1, "error", None,
                                            reason="Adapter failure: " + type(error).__name__) for s in symbols))


class MockRiskEstimator:
    """Explicit fixture risk, NOT an estimate from historical observations."""
    def __init__(self, symbols, interval_ms):
        self.symbols, self.interval_ms = tuple(symbols), interval_ms

    def estimate(self, context):
        unavailable = any(d["required"] and d["status"] != "ready" for d in context["datasets"].values())
        return RiskSnapshot(context["context_id"] + ":risk", context["asof_ms"],
                            "missing" if unavailable else "ready", "mock-fixture-not-historical",
                            "demo-0.1", self.symbols, self.interval_ms * 2, self.interval_ms,
                            0 if unavailable else 2,
                            {} if unavailable else {s: "0.01" for s in self.symbols},
                            () if unavailable else tuple(tuple("0.0001" if i == j else "0"
                                                               for j in range(len(self.symbols)))
                                                         for i in range(len(self.symbols))),
                            errors=("Unavailable required data",) if unavailable else ())
