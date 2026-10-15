"""Demonstration producers and the checked ForecastProducer boundary."""
import copy
from decimal import Decimal

from .context import SOURCE
from .contracts import Forecast, ForecastBatch, RiskSnapshot, positive_ms
from .interfaces import DataRequest, identity, timestamp
from .producers import validate_producer, validate_requests


class RuleForecastProducer:
    producer_id = "rule-demo"
    producer_version = "0.1"

    def __init__(self, symbols, horizon_ms, ttl_ms, *, clock=None):
        self.symbols = tuple(symbols)
        positive_ms(horizon_ms)
        positive_ms(ttl_ms)
        self.horizon_ms, self.ttl_ms = horizon_ms, ttl_ms
        self.clock = clock

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
                             self.producer_id, self.producer_version, self.clock() if self.clock else now, tuple(items))


class MockAgentForecastProducer(RuleForecastProducer):
    producer_id = "mock-agent"

    def __init__(self, symbols, horizon_ms, ttl_ms, abstain_symbols, *, clock=None):
        super().__init__(symbols, horizon_ms, ttl_ms, clock=clock)
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


class ForecastUnavailable(ValueError):
    """Structured scheduling failure; deliberately separate from the forecast/0.1 wire contract."""
    def __init__(self, reason_code, reason):
        super().__init__(reason)
        self.reason_code = reason_code

    def to_dict(self):
        return {"reason_code": self.reason_code, "reason": str(self)}


def checked_forecast(producer, context, *, clock=None, max_inference_ms=60000,
                     max_input_age_ms=60000, requirements=None, raise_on_error=None):
    """Explicit trusted receive clock for delayed adapters; two-argument fixture calls remain compatible.

    Budgets reject late returns; adapters must bound their own blocking I/O. No thread is forcefully killed.
    """
    strict = (clock is not None) if raise_on_error is None else raise_on_error
    clock = clock or (lambda: context["asof_ms"])
    positive_ms(max_inference_ms)
    positive_ms(max_input_age_ms)
    declared = None

    def fail(code, message):
        raise ForecastUnavailable(code, message)

    try:
        try:
            validate_producer(producer)
            declared = validate_requests(producer.requirements() if requirements is None else requirements)
        except Exception as error:
            fail("INVALID_PRODUCER", "Invalid producer declaration: " + type(error).__name__)
        expected_identity = (producer.producer_id, producer.producer_version)
        identity(context["context_id"])
        timestamp(context["asof_ms"])
        if context["protocol_version"] != "1.0":
            fail("INVALID_CONTEXT", "Unsupported context protocol")
        if context["account"] is not None:
            timestamp(context["account"]["observed_ms"])
            if context["account"]["observed_ms"] > context["asof_ms"]:
                fail("INVALID_CONTEXT", "Account snapshot is after the context cutoff")
        for report in context["execution_reports"]:
            timestamp(report["observed_ms"])
            if report["observed_ms"] > context["asof_ms"]:
                fail("INVALID_CONTEXT", "Execution feedback is after the context cutoff")
        started = clock()
        timestamp(started)
        if not 0 <= started - context["asof_ms"] <= max_input_age_ms:
            fail("STALE_INPUT", "Context future or beyond input age limit")
        allowed_datasets = {r.dataset_id: r for r in declared}
        for request in declared:
            dataset = context["datasets"].get(request.dataset_id)
            if dataset is None or any(dataset[k] != expected for k, expected in (
                ("source", request.source), ("kind", request.kind), ("parameters", request.parameters),
                ("required", request.required),
            )):
                fail("INVALID_CONTEXT", "Dataset declaration/source mismatch: " + request.dataset_id)
            if request.required and dataset["status"] != "ready":
                fail("DATA_UNAVAILABLE", "Required dataset unavailable: " + request.dataset_id + ":" +
                     dataset["status"] + ":" + str(dataset.get("error")))
            if dataset["status"] == "ready" and not dataset["records"]:
                fail("INVALID_CONTEXT", "Ready dataset has no records")
            for row in dataset["records"]:
                timestamp(row["event_ms"])
                timestamp(row["received_ms"])
                if not row["event_ms"] <= row["received_ms"] <= context["asof_ms"]:
                    fail("INVALID_CONTEXT", "Dataset contains future information")
                if row["symbol"] not in request.symbols or set(row["data"]) != set(request.fields):
                    fail("INVALID_CONTEXT", "Dataset symbols/fields do not match the declared request")
        adapter_error = None
        try:
            batch = producer.forecast(copy.deepcopy(context))
        except Exception as error:
            adapter_error = error
        received = clock()
        timestamp(received)
        if received < started:
            fail("INVALID_CLOCK", "Trusted clock moved backwards")
        if received - started > max_inference_ms:
            fail("INFERENCE_TIMEOUT", "Inference exceeded configured receive budget")
        if received - context["asof_ms"] > max_input_age_ms:
            fail("STALE_INPUT", "Context too old at receive time")
        if adapter_error is not None:
            fail("ADAPTER_TIMEOUT" if isinstance(adapter_error, TimeoutError) else "ADAPTER_ERROR",
                 "Adapter failure: " + type(adapter_error).__name__)
        # Revalidate the JSON boundary, including mutations inside frozen objects.
        batch = ForecastBatch.from_dict(batch.to_dict())
        if (batch.context_id != context["context_id"]
                or (batch.producer_id, batch.producer_version) != expected_identity
                or (producer.producer_id, producer.producer_version) != expected_identity):
            fail("IDENTITY_MISMATCH", "Producer/context identity mismatch")
        if not context["asof_ms"] <= batch.created_ms <= received:
            fail("INVALID_TIME", "Batch created before context or after trusted receive time")
        allowed = {s for r in declared for s in r.symbols}
        if any(i.symbol not in allowed for i in batch.items):
            fail("UNDECLARED_ASSET", "Undeclared forecast asset")
        for item in batch.items:
            if item.asof_ms != context["asof_ms"]:
                fail("INVALID_TIME", "Forecast information cutoff must match its context")
            if item.expires_ms <= received:
                fail("EXPIRED", "Forecast expired before reception")
            for ref in item.evidence_refs:
                if ref not in allowed_datasets or context["datasets"][ref]["status"] != "ready":
                    fail("INVALID_EVIDENCE", "Unknown, undeclared or unavailable evidence reference")
        return batch
    except Exception as error:
        failure = error if isinstance(error, ForecastUnavailable) else ForecastUnavailable(
            "INVALID_FORECAST", "Invalid adapter output: " + type(error).__name__)
        if strict or declared is None:
            if failure is error:
                raise
            raise failure from error
        # Legacy two-argument fixture API keeps explicit error items. Never call requirements() again.
        try:
            now = clock()
            symbols = sorted({s for r in declared for s in r.symbols})
            return ForecastBatch(context["context_id"] + ":" + producer.producer_id, context["context_id"],
                                 producer.producer_id, producer.producer_version, now,
                                 tuple(Forecast(s, context["asof_ms"], now + 1, 1, "error", None,
                                                reason=failure.reason_code + ": " + str(failure)) for s in symbols))
        except Exception:
            raise failure from None


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
