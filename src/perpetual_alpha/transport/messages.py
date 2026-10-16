"""Explicit request/response bodies for the future HTTP adapter."""
import json
from dataclasses import dataclass
from typing import Literal

from ..contracts import ForecastBatch
from ..interfaces import DataRequest
from ..runtime.contracts import (
    TRANSPORT_VERSION,
    AnalysisJob,
    ErrorCode,
    JobStatus,
    Lease,
    LeaseRef,
    ProcessingRecord,
    SubmissionReceipt,
    WireContract,
)


@dataclass(frozen=True)
class ClaimRequest(WireContract):
    request_id: str
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION


@dataclass(frozen=True)
class ClaimResponse(WireContract):
    job: AnalysisJob
    lease: Lease
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION

    def check(self):
        if (self.job.status != "running" or self.lease.ref.job_id != self.job.job_id
                or (self.lease.ref.attempt, self.lease.ref.lease_epoch) != (self.job.attempt, self.job.lease_epoch)
                or not self.job.created_ms <= self.lease.claimed_ms < self.lease.expires_ms <= self.job.deadline_ms):
            raise ValueError("Claim job/lease mismatch")


@dataclass(frozen=True)
class HeartbeatRequest(WireContract):
    lease: LeaseRef
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION


@dataclass(frozen=True)
class HeartbeatResponse(WireContract):
    lease: Lease
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION


@dataclass(frozen=True)
class ForecastSubmission(WireContract):
    submission_id: str
    lease: LeaseRef
    forecast_batch: ForecastBatch
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION


@dataclass(frozen=True)
class SubmissionResponse(WireContract):
    receipt: SubmissionReceipt
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION


@dataclass(frozen=True)
class CompleteRequest(WireContract):
    completion_id: str
    lease: LeaseRef
    outcome: Literal["no_forecast", "failed"]
    reason: str
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION


@dataclass(frozen=True)
class CompleteResponse(WireContract):
    job_id: str
    completion_id: str
    status: Literal["completed_without_forecast", "failed"]
    received_ms: int
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION


@dataclass(frozen=True)
class DataQueryRequest(WireContract):
    request_id: str
    lease: LeaseRef
    data_request: DataRequest
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION


@dataclass(frozen=True)
class DataRecord(WireContract):
    record_id: str
    symbol: str
    event_ms: int
    received_ms: int
    data: dict[str, str]

    def check(self):
        from ..contracts import symbol
        symbol(self.symbol)
        if self.event_ms > self.received_ms or not self.data:
            raise ValueError("Invalid data record")


@dataclass(frozen=True)
class DataQueryResponse(WireContract):
    request_id: str
    dataset_id: str
    source: str
    kind: str
    schema_version: str
    asof_ms: int
    status: Literal["ready", "missing", "stale", "error"]
    units: dict[str, str]
    records: tuple[DataRecord, ...]
    truncated: bool
    error: str | None
    evidence_ref: str | None
    payload_digest: str
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION

    def check(self):
        if any(r.received_ms > self.asof_ms for r in self.records):
            raise ValueError("Future data response")
        if self.status == "ready" and (not self.records or self.error is not None):
            raise ValueError("Ready response requires records and no error")
        if self.evidence_ref is not None and (self.status != "ready" or self.evidence_ref != self.dataset_id):
            raise ValueError("Only ready immutable dataset IDs may be cited")
        if len(self.payload_digest) != 64 or any(c not in "0123456789abcdef" for c in self.payload_digest):
            raise ValueError("SHA-256 required")


@dataclass(frozen=True)
class FeedbackResponse(WireContract):
    job_id: str
    job_status: JobStatus
    revision: int
    receipt: SubmissionReceipt | None
    processing: ProcessingRecord | None
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION

    def check(self):
        if self.revision < 1:
            raise ValueError("Positive feedback revision required")
        if (self.job_status == "submitted") != (self.receipt is not None):
            raise ValueError("Only submitted jobs have forecast receipts")
        if self.processing is not None and self.receipt is None:
            raise ValueError("Processing requires accepted forecast")
        if any(item is not None and item.job_id != self.job_id for item in (self.receipt, self.processing)):
            raise ValueError("Cross-job feedback")
        if self.processing is not None and self.processing.processing_id != self.receipt.processing_id:
            raise ValueError("Cross-processing feedback")


@dataclass(frozen=True)
class HealthResponse(WireContract):
    status: Literal["ready", "unavailable"]
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION


@dataclass(frozen=True)
class ErrorResponse(WireContract):
    request_id: str
    code: ErrorCode
    message: str
    retryable: bool
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION


def parse_message(cls, raw, *, max_bytes):
    """Bounded strict UTF-8 JSON decoding. HTTP adapter must additionally bound stream/depth/rate."""
    if type(max_bytes) is not int or max_bytes < 1 or not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise ValueError("Invalid/oversized JSON body")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite JSON constant")

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant)
        return cls.from_dict(payload)
    except (RecursionError, UnicodeError, TypeError) as error:
        raise ValueError("Invalid transport JSON") from error
