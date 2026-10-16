"""Pure preflight validation. Does NOT accept/persist a submission, issue 202 or execute anything."""
import copy
import hmac
from dataclasses import asdict, dataclass

from ..contracts import ForecastBatch
from ..forecast_validation import ForecastUnavailable, validate_context, validate_forecast_result
from ..journal import digest
from ..producers import validate_requests
from ..transport.messages import ForecastSubmission
from .contracts import AnalysisJob, Lease, ProcessingStatus, Role, WireContract, decode


class IngressRejected(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Principal(WireContract):
    """Server-authenticated identity, NEVER decoded from a client JSON field."""
    worker_id: str
    producer_id: str
    producer_version: str
    role: Role


@dataclass(frozen=True)
class ValidatedForecast:
    batch: ForecastBatch
    payload_digest: str
    manifest_digest: str
    initial_processing_status: ProcessingStatus
    reason_code: str


def input_digest(context, requests):
    """Server seals this when granting the frozen context, not from the submission body."""
    return digest({"context": context, "requests": [asdict(r) for r in requests]})


def validate_lease(job, lease, ref, principal, now_ms):
    """Ownership guard for heartbeat/query/complete/new submit; not authentication itself."""
    job = AnalysisJob.from_dict(job.to_dict())
    lease = Lease.from_dict(lease.to_dict())
    ref = type(lease.ref).from_dict(ref.to_dict())
    principal = Principal.from_dict(principal.to_dict())
    decode(int, now_ms)
    if (principal.producer_id, principal.producer_version, principal.role) != (
            job.producer_id, job.producer_version, job.role) or principal.worker_id != lease.worker_id:
        raise IngressRejected("FORBIDDEN", "Authenticated producer/worker/role mismatch")
    if (job.status != "running" or (ref.job_id, ref.attempt, ref.lease_epoch) != (
            job.job_id, job.attempt, job.lease_epoch)
            or (ref.job_id, ref.attempt, ref.lease_epoch) != (
                lease.ref.job_id, lease.ref.attempt, lease.ref.lease_epoch)
            or not hmac.compare_digest(ref.lease_token.encode(), lease.ref.lease_token.encode())):
        raise IngressRejected("LEASE_LOST", "Not the current running lease")
    if not job.created_ms <= lease.claimed_ms < lease.expires_ms <= job.deadline_ms:
        raise IngressRejected("INVALID_REQUEST", "Lease outside job lifetime")
    if now_ms < lease.claimed_ms:
        raise IngressRejected("INVALID_REQUEST", "Receive clock before claim")
    if now_ms >= job.deadline_ms:
        raise IngressRejected("EXPIRED", "Job deadline reached")
    if now_ms >= lease.expires_ms:
        raise IngressRejected("LEASE_LOST", "Lease expired")


def validate_ingress(submission, *, job, lease, principal, context, requests, manifest_digest,
                     received_ms, current_control_epoch, accepted_asof_ms=None):
    """New submissions only. PR1 must atomically recheck ownership/watermark and commit result+outbox.

    Already-accepted retries must instead replay an authenticated persisted receipt in PR1;
    this function never treats an expired lease as permission to create a new result.
    """
    try:
        submission = ForecastSubmission.from_dict(submission.to_dict())
        job = AnalysisJob.from_dict(job.to_dict())
        lease = Lease.from_dict(lease.to_dict())
        principal = Principal.from_dict(principal.to_dict())
        decode(int, received_ms)
        decode(int, current_control_epoch)
        if accepted_asof_ms is not None:
            decode(int, accepted_asof_ms)
        validate_lease(job, lease, submission.lease, principal, received_ms)
        if job.role == "control" and (job.control_epoch != current_control_epoch or (
                accepted_asof_ms is not None and job.asof_ms <= accepted_asof_ms)):
            raise IngressRejected("STALE_RESULT", "Control epoch or accepted result watermark changed")
        if received_ms - lease.claimed_ms > job.limits.max_inference_ms:
            raise ForecastUnavailable("INFERENCE_TIMEOUT", "Analysis exceeded claim-to-receive budget")
        if not 0 <= received_ms - job.asof_ms <= job.limits.max_input_age_ms:
            raise ForecastUnavailable("STALE_INPUT", "Frozen input too old at reception")
        requests = validate_requests(requests)
        if len({r.dataset_id for r in requests}) != len(requests):
            raise IngressRejected("INVALID_CONTEXT", "Ambiguous manifest dataset IDs")
        context = copy.deepcopy(context)
        if context["context_id"] != job.context_id or context["asof_ms"] != job.asof_ms:
            raise IngressRejected("INVALID_CONTEXT", "Job/context cutoff mismatch")
        if set(context["datasets"]) != {r.dataset_id for r in requests}:
            raise IngressRejected("INVALID_CONTEXT", "Unscoped manifest datasets")
        if input_digest(context, requests) != manifest_digest:
            raise IngressRejected("INVALID_CONTEXT", "Frozen manifest changed")
        validate_context(context, requests)
        batch = validate_forecast_result(submission.forecast_batch, context, requests,
                                         (job.producer_id, job.producer_version), received_ms,
                                         symbols=job.symbols)
        if batch.created_ms < lease.claimed_ms:
            raise ForecastUnavailable("INVALID_TIME", "Batch completed before this analysis attempt was claimed")
        blocked = any(item.status == "error" for item in batch.items)
        status = "blocked" if blocked else "completed" if job.role == "shadow" else "pending"
        reason = "PRODUCER_ERROR" if blocked else "SHADOW_ONLY" if job.role == "shadow" else "OK"
        return ValidatedForecast(batch, digest(batch.to_dict()), manifest_digest, status, reason)
    except (IngressRejected, ForecastUnavailable):
        raise
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError) as error:
        raise IngressRejected("INVALID_REQUEST", "Malformed ingress input") from error
