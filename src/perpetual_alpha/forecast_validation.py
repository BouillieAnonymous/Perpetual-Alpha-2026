"""Pure Forecast validation shared by synchronous adapters and remote ingress. No I/O."""
from .contracts import ForecastBatch
from .interfaces import identity, timestamp


class ForecastUnavailable(ValueError):
    """Structured scheduling failure; deliberately separate from the forecast/0.1 wire contract."""
    def __init__(self, reason_code, reason):
        super().__init__(reason)
        self.reason_code = reason_code

    def to_dict(self):
        return {"reason_code": self.reason_code, "reason": str(self)}


def _fail(code, message):
    raise ForecastUnavailable(code, message)


def validate_context(context, declared):
    validate_context_header(context)
    validate_datasets(context, declared)


def validate_context_header(context):
    identity(context["context_id"])
    timestamp(context["asof_ms"])
    if context["protocol_version"] != "1.0":
        _fail("INVALID_CONTEXT", "Unsupported context protocol")
    if context["account"] is not None:
        timestamp(context["account"]["observed_ms"])
        if context["account"]["observed_ms"] > context["asof_ms"]:
            _fail("INVALID_CONTEXT", "Account snapshot is after the context cutoff")
    for report in context["execution_reports"]:
        timestamp(report["observed_ms"])
        if report["observed_ms"] > context["asof_ms"]:
            _fail("INVALID_CONTEXT", "Execution feedback is after the context cutoff")


def validate_datasets(context, declared):
    for request in declared:
        dataset = context["datasets"].get(request.dataset_id)
        if dataset is None or any(dataset[k] != expected for k, expected in (
            ("source", request.source), ("kind", request.kind), ("parameters", request.parameters),
            ("required", request.required),
        )):
            _fail("INVALID_CONTEXT", "Dataset declaration/source mismatch: " + request.dataset_id)
        if request.required and dataset["status"] != "ready":
            _fail("DATA_UNAVAILABLE", "Required dataset unavailable: " + request.dataset_id + ":" +
                 dataset["status"] + ":" + str(dataset.get("error")))
        if dataset["status"] == "ready" and not dataset["records"]:
            _fail("INVALID_CONTEXT", "Ready dataset has no records")
        for row in dataset["records"]:
            timestamp(row["event_ms"])
            timestamp(row["received_ms"])
            if not row["event_ms"] <= row["received_ms"] <= context["asof_ms"]:
                _fail("INVALID_CONTEXT", "Dataset contains future information")
            if row["symbol"] not in request.symbols or set(row["data"]) != set(request.fields):
                _fail("INVALID_CONTEXT", "Dataset symbols/fields do not match the declared request")


def validate_forecast_result(batch, context, declared, expected_identity, received, *, symbols=None,
                             actual_identity=None):
    # Revalidate the JSON boundary, including mutations inside frozen objects.
    batch = ForecastBatch.from_dict(batch.to_dict())
    if (batch.context_id != context["context_id"]
            or (batch.producer_id, batch.producer_version) != expected_identity
            or (actual_identity is not None and actual_identity != expected_identity)):
        _fail("IDENTITY_MISMATCH", "Producer/context identity mismatch")
    if not context["asof_ms"] <= batch.created_ms <= received:
        _fail("INVALID_TIME", "Batch created before context or after trusted receive time")
    allowed_datasets = {r.dataset_id: r for r in declared}
    allowed = {s for r in declared for s in r.symbols} if symbols is None else set(symbols)
    if any(i.symbol not in allowed for i in batch.items):
        _fail("UNDECLARED_ASSET", "Undeclared forecast asset")
    for item in batch.items:
        if item.asof_ms != context["asof_ms"]:
            _fail("INVALID_TIME", "Forecast information cutoff must match its context")
        if item.expires_ms <= received:
            _fail("EXPIRED", "Forecast expired before reception")
        for ref in item.evidence_refs:
            if ref not in allowed_datasets or context["datasets"][ref]["status"] != "ready":
                _fail("INVALID_EVIDENCE", "Unknown, undeclared or unavailable evidence reference")
    return batch
