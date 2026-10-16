"""Pure agent-transport/0.1 domain records and transition rules; no database or HTTP."""
from dataclasses import dataclass, fields, replace
from typing import Literal, get_args, get_origin, get_type_hints

from ..contracts import JsonContract, symbol
from ..interfaces import DataRequest

TRANSPORT_VERSION = "agent-transport/0.1"
JobStatus = Literal["queued", "running", "submitted", "completed_without_forecast", "failed", "expired"]
ProcessingStatus = Literal["pending", "running", "completed", "blocked", "failed"]
Role = Literal["control", "shadow"]
ErrorCode = Literal[
    "INVALID_REQUEST", "UNSUPPORTED_VERSION", "UNAUTHENTICATED", "FORBIDDEN", "NOT_FOUND",
    "ID_CONFLICT", "LEASE_LOST", "EXPIRED", "STALE_RESULT", "INVALID_FORECAST", "INVALID_CONTEXT",
    "DATA_UNAVAILABLE", "LIMIT_EXCEEDED", "UNAVAILABLE",
]


def decode(annotation, value):
    """Strict JSON-shaped decoding, also used to revalidate mutable nested objects."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        if value not in args or type(value) is not type(args[0]):
            raise ValueError("Unsupported literal/version")
        return value
    if annotation is type(None):
        if value is not None:
            raise ValueError("Expected null")
        return None
    if origin is not None and type(None) in args:
        return None if value is None else decode(next(a for a in args if a is not type(None)), value)
    if origin is tuple:
        if not isinstance(value, (tuple, list)):
            raise ValueError("Expected array")
        return tuple(decode(args[0], item) for item in value)
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        return {decode(args[0], k): decode(args[1], v) for k, v in value.items()}
    if annotation in (str, int, bool):
        if type(value) is not annotation:
            raise ValueError("Wrong field type")
        if annotation is str and (not value.strip() or len(value) > 4096):
            raise ValueError("Text must be nonempty and bounded")
        if annotation is int and not 0 <= value <= 2**53 - 1:
            raise ValueError("Integer outside interoperable JSON range")
        return value
    if annotation is DataRequest:
        from ..producers import validate_requests
        if isinstance(value, DataRequest):
            value = {f.name: getattr(value, f.name) for f in fields(DataRequest)}
        if not isinstance(value, dict) or set(value) != {f.name for f in fields(DataRequest)}:
            raise ValueError("Exact DataRequest fields required")
        for name in ("lookback_ms", "max_records", "max_age_ms"):
            decode(int, value[name])
        for name in ("dataset_id", "source", "kind"):
            decode(str, value[name])
        for name in ("symbols", "fields"):
            decode(tuple[str, ...], value[name])
        request = DataRequest(**value)
        validate_requests((request,))
        if not request.symbols or not request.fields:
            raise ValueError("Nonempty request symbols/fields required")
        for item in request.symbols:
            symbol(item)
        return request
    if isinstance(value, annotation):
        value = value.to_dict()
    return annotation.from_dict(value)


class WireContract(JsonContract):
    @classmethod
    def from_dict(cls, payload):
        expected = {f.name for f in fields(cls)}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("Missing or unexpected transport fields")
        return cls(**payload)

    def __post_init__(self):
        for name, annotation in get_type_hints(type(self)).items():
            object.__setattr__(self, name, decode(annotation, getattr(self, name)))
        self.check()

    def check(self):
        pass


@dataclass(frozen=True)
class JobLimits(WireContract):
    max_queries: int
    max_records_per_query: int
    max_bytes: int
    max_attempts: int
    max_input_age_ms: int
    max_inference_ms: int

    def check(self):
        if any(getattr(self, f.name) == 0 for f in fields(self)):
            raise ValueError("Positive explicit budgets required")


@dataclass(frozen=True)
class AnalysisJob(WireContract):
    job_id: str
    context_id: str
    producer_id: str
    producer_version: str
    control_scope: str
    control_epoch: int
    role: Role
    symbols: tuple[str, ...]
    asof_ms: int
    created_ms: int
    deadline_ms: int
    status: JobStatus
    attempt: int
    lease_epoch: int
    limits: JobLimits
    transport_version: Literal["agent-transport/0.1"] = TRANSPORT_VERSION

    def check(self):
        if not self.asof_ms <= self.created_ms < self.deadline_ms or self.control_epoch < 1:
            raise ValueError("Invalid job time or control epoch")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Nonempty unique forecast allowlist required")
        for item in self.symbols:
            symbol(item)
        if self.attempt > self.limits.max_attempts or self.lease_epoch < self.attempt:
            raise ValueError("Invalid attempt/fencing epoch")
        if self.status not in {"queued", "expired"} and self.attempt == 0:
            raise ValueError("Started job needs an attempt")


@dataclass(frozen=True)
class LeaseRef(WireContract):
    job_id: str
    attempt: int
    lease_epoch: int
    lease_token: str

    def check(self):
        if self.attempt < 1 or self.lease_epoch < self.attempt:
            raise ValueError("Invalid lease identity")


@dataclass(frozen=True)
class Lease(WireContract):
    ref: LeaseRef
    worker_id: str
    claimed_ms: int
    expires_ms: int

    def check(self):
        if self.expires_ms <= self.claimed_ms:
            raise ValueError("Lease expiry must follow claim")


@dataclass(frozen=True)
class ProcessingRecord(WireContract):
    processing_id: str
    job_id: str
    status: ProcessingStatus
    reason_code: str
    updated_ms: int
    revision: int
    decision_id: str | None
    verdict_id: str | None
    report_id: str | None

    def check(self):
        if self.revision < 1:
            raise ValueError("Positive processing revision required")
        if self.status in {"blocked", "failed"} and self.reason_code == "OK":
            raise ValueError("Blocked/failed processing requires an explicit reason")


@dataclass(frozen=True)
class SubmissionReceipt(WireContract):
    job_id: str
    submission_id: str
    forecast_batch_id: str
    payload_digest: str
    received_ms: int
    processing_id: str

    def check(self):
        if len(self.payload_digest) != 64 or any(c not in "0123456789abcdef" for c in self.payload_digest):
            raise ValueError("SHA-256 required")


def transition_job(job, event, now_ms):
    """Pure transition guard. PR1 must combine this with lease/identity checks in one transaction."""
    job = AnalysisJob.from_dict(job.to_dict())
    decode(int, now_ms)
    if now_ms < job.created_ms:
        raise ValueError("Clock before job creation")
    if event == "expire" and job.status in {"queued", "running"} and now_ms >= job.deadline_ms:
        return replace(job, status="expired")
    if now_ms >= job.deadline_ms:
        raise ValueError("Deadline reached; only uncompleted jobs may expire")
    if event == "claim" and job.status == "queued" and job.attempt < job.limits.max_attempts:
        return replace(job, status="running", attempt=job.attempt + 1, lease_epoch=job.lease_epoch + 1)
    transitions = {"submit": "submitted", "no_forecast": "completed_without_forecast", "fail": "failed"}
    if job.status == "running" and event in transitions:
        return replace(job, status=transitions[event])
    if job.status == "running" and event == "lease_lost":
        return replace(job, status="queued" if job.attempt < job.limits.max_attempts else "failed")
    raise ValueError("Illegal job transition")


def transition_processing(record, event, now_ms, *, reason_code=None):
    record = ProcessingRecord.from_dict(record.to_dict())
    decode(int, now_ms)
    allowed = {"pending": {"start": "running"},
               "running": {"recover": "pending", "complete": "completed", "block": "blocked", "fail": "failed"}}
    if now_ms < record.updated_ms or event not in allowed.get(record.status, {}):
        raise ValueError("Illegal processing transition/time")
    return replace(record, status=allowed[record.status][event], updated_ms=now_ms, revision=record.revision + 1,
                   reason_code=record.reason_code if reason_code is None else reason_code)
