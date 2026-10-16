"""Transactional job admission, ownership, immutable results and outbox. No network or execution."""
import json
import secrets
import sqlite3
from dataclasses import asdict, replace

from ..interfaces import DataRequest
from ..journal import canonical, digest
from ..producers import validate_requests
from ..transport.messages import (
    ClaimRequest,
    ClaimResponse,
    CompleteRequest,
    CompleteResponse,
    FeedbackResponse,
    ForecastSubmission,
)
from .contracts import (
    AnalysisJob,
    Lease,
    LeaseRef,
    ProcessingRecord,
    SubmissionReceipt,
    decode,
    transition_job,
)
from .ingress import IngressRejected, Principal, input_digest, validate_ingress, validate_lease


def reject(code, message):
    raise IngressRejected(code, message)


class JobQueue:
    def __init__(self, store, *, max_active_jobs=1000):
        decode(int, max_active_jobs)
        if max_active_jobs < 1:
            raise ValueError("Positive queue admission bound required")
        self.store, self.max_active_jobs = store, max_active_jobs

    def configure_worker(self, principal, *, enabled=True):
        """Trusted Core configuration, not an Agent-accessible registration endpoint."""
        principal = Principal.from_dict(principal.to_dict())
        if type(enabled) is not bool:
            raise ValueError("Boolean enabled flag required")
        with self.store.transaction() as (c, now):
            c.execute("INSERT INTO workers VALUES(?,?,?) ON CONFLICT(worker_id) DO UPDATE SET "
                      "principal=excluded.principal,enabled=excluded.enabled",
                      (principal.worker_id, canonical(principal.to_dict()), int(enabled)))
            self.store.event(c, now, "worker-configured", {"worker_id": principal.worker_id, "enabled": enabled})

    def configure_control(self, scope, producer_id, producer_version, epoch):
        """Explicit monotonic control switch. Preserve scope watermarks across upgrades."""
        for value in (scope, producer_id, producer_version):
            decode(str, value)
        decode(int, epoch)
        if epoch < 1:
            raise ValueError("Positive epoch required")
        with self.store.transaction() as (c, now):
            old = c.execute("SELECT * FROM controls WHERE scope=?", (scope,)).fetchone()
            if old and (old["producer_id"], old["producer_version"], old["epoch"]) == (
                    producer_id, producer_version, epoch):
                return
            if old and epoch <= old["epoch"]:
                reject("ID_CONFLICT", "Control switches require a higher epoch")
            c.execute("INSERT INTO controls(scope,producer_id,producer_version,epoch) VALUES(?,?,?,?) "
                      "ON CONFLICT(scope) DO UPDATE SET producer_id=excluded.producer_id,"
                      "producer_version=excluded.producer_version,epoch=excluded.epoch",
                      (scope, producer_id, producer_version, epoch))
            self.store.event(c, now, "control-configured", {"scope": scope, "epoch": epoch})

    @staticmethod
    def _auth(c, principal):
        principal = Principal.from_dict(principal.to_dict())
        row = c.execute("SELECT principal,enabled FROM workers WHERE worker_id=?", (principal.worker_id,)).fetchone()
        if not row or not row["enabled"] or row["principal"] != canonical(principal.to_dict()):
            reject("FORBIDDEN", "Worker is unregistered, revoked or has changed identity")
        return principal

    @staticmethod
    def _control(c, job):
        control = c.execute("SELECT * FROM controls WHERE scope=?", (job.control_scope,)).fetchone()
        if job.role == "control" and (not control or (
                control["producer_id"], control["producer_version"], control["epoch"]) != (
                    job.producer_id, job.producer_version, job.control_epoch)):
            reject("STALE_RESULT", "Control identity/epoch is no longer current")
        return control

    @staticmethod
    def _job(c, job_id):
        row = c.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            reject("NOT_FOUND", "Unknown job")
        return row, AnalysisJob.from_dict(json.loads(row["job"]))

    @classmethod
    def _owned(cls, c, job_id, principal):
        row, job = cls._job(c, job_id)
        if (job.producer_id, job.producer_version, job.role, row["worker_id"]) != (
                principal.producer_id, principal.producer_version, principal.role, principal.worker_id):
            reject("FORBIDDEN", "Job is not owned by this authenticated worker")
        return row, job

    @staticmethod
    def _write_job(c, job, *, worker_id, lease):
        c.execute("UPDATE jobs SET status=?,worker_id=?,job=?,lease=? WHERE job_id=?",
                  (job.status, worker_id, canonical(job.to_dict()),
                   canonical(lease.to_dict()) if lease is not None else None, job.job_id))

    def _sweep(self, c, now):
        counts = {"expired": 0, "requeued": 0, "failed": 0}
        for row in c.execute("SELECT * FROM jobs WHERE status IN ('queued','running')").fetchall():
            job = AnalysisJob.from_dict(json.loads(row["job"]))
            if now >= job.deadline_ms:
                job = transition_job(job, "expire", now)
            elif job.status == "running" and now >= json.loads(row["lease"])["expires_ms"]:
                job = transition_job(job, "lease_lost", now)
            else:
                continue
            self._write_job(c, job, worker_id=None, lease=None)
            kind = "requeued" if job.status == "queued" else job.status
            counts[kind] += 1
            self.store.event(c, now, "job-" + kind, {"job_id": job.job_id, "attempt": job.attempt})
        return counts

    def recover_jobs(self):
        """Reclaim only genuinely expired leases; Core restart alone does not revoke a live worker."""
        with self.store.transaction() as (c, now):
            return self._sweep(c, now)

    @staticmethod
    def _busy_scope(c, scope, *, include_queued=False):
        if c.execute("SELECT 1 FROM jobs j WHERE scope=? AND role='control' AND (status='running' OR "
                     "(? AND status='queued' AND EXISTS (SELECT 1 FROM controls c WHERE c.scope=j.scope "
                     "AND c.producer_id=j.producer_id AND c.producer_version=j.producer_version "
                     "AND c.epoch=j.control_epoch AND (c.accepted_asof_ms IS NULL OR "
                     "j.asof_ms>c.accepted_asof_ms)))) LIMIT 1", (scope, int(include_queued))).fetchone():
            return True
        return bool(c.execute("SELECT 1 FROM processing WHERE scope=? AND role='control' "
                              "AND status IN ('pending','running') LIMIT 1", (scope,)).fetchone())

    def _insert(self, c, now, job, context, requests, *, schedule_group=None, schedule_tick=None):
        job = AnalysisJob.from_dict(job.to_dict())
        if job.status != "queued" or job.attempt or job.lease_epoch:
            reject("INVALID_REQUEST", "Only initial queued jobs may be admitted")
        requests = validate_requests(requests)
        context = json.loads(canonical(context))
        if (context["context_id"], context["asof_ms"], context["protocol_version"]) != (
                job.context_id, job.asof_ms, "1.0"):
            reject("INVALID_CONTEXT", "Context must match job identity and frozen cutoff")
        if len({r.dataset_id for r in requests}) != len(requests) or set(context["datasets"]) != {
                r.dataset_id for r in requests}:
            reject("INVALID_CONTEXT", "Exact unique dataset scope required")
        # Validate all visible record/feedback times even if required data are unavailable.
        from ..forecast_validation import validate_context
        for request in requests:
            if (context["datasets"][request.dataset_id]["required"] != request.required
                    or context["datasets"][request.dataset_id]["status"] not in {"ready", "missing", "stale", "error"}):
                reject("INVALID_CONTEXT", "Manifest readiness/required declaration mismatch")
        optional = tuple(replace(r, required=False) for r in requests)
        validation_view = json.loads(canonical(context))
        for dataset in validation_view["datasets"].values():
            dataset["required"] = False
        validate_context(validation_view, optional)
        manifest = {"context": context, "requests": [asdict(r) for r in requests]}
        fingerprint = digest({"job": job.to_dict(), "manifest": manifest,
                              "schedule_group": schedule_group, "schedule_tick": schedule_tick})
        existing = c.execute("SELECT create_digest FROM jobs WHERE job_id=?", (job.job_id,)).fetchone()
        if existing:
            if existing[0] != fingerprint:
                reject("ID_CONFLICT", "Immutable job identity conflict")
            return self._job(c, job.job_id)[1]
        if not job.created_ms <= now < job.deadline_ms:
            reject("EXPIRED", "Only fresh jobs may be newly admitted")
        control = self._control(c, job)
        if job.role == "control" and control["accepted_asof_ms"] is not None and job.asof_ms <= control["accepted_asof_ms"]:
            reject("STALE_RESULT", "No new job at or behind the accepted control watermark")
        active = c.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        pending = c.execute("SELECT count(*) FROM processing WHERE status IN ('pending','running')").fetchone()[0]
        if active + pending >= self.max_active_jobs:
            reject("LIMIT_EXCEEDED", "Runtime admission backlog is full")
        try:
            c.execute("INSERT INTO jobs(job_id,context_id,producer_id,producer_version,role,scope,control_epoch,"
                      "asof_ms,deadline_ms,status,job,manifest,manifest_digest,create_digest,schedule_group,schedule_tick) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (job.job_id, job.context_id, job.producer_id, job.producer_version, job.role, job.control_scope,
                       job.control_epoch, job.asof_ms, job.deadline_ms, job.status, canonical(job.to_dict()),
                       canonical(manifest), input_digest(context, requests), fingerprint, schedule_group, schedule_tick))
        except sqlite3.IntegrityError as error:
            raise IngressRejected("ID_CONFLICT", "Context or scheduled job identity conflict") from error
        self.store.event(c, now, "job-created", {"job_id": job.job_id})
        return job

    def enqueue(self, job, context, requests):
        with self.store.transaction() as (c, now):
            self._sweep(c, now)
            return self._insert(c, now, job, context, requests)

    def claim(self, principal, request, *, lease_ms):
        request = ClaimRequest.from_dict(request.to_dict())
        decode(int, lease_ms)
        if lease_ms < 1:
            raise ValueError("Positive lease duration required")
        with self.store.transaction() as (c, now):
            principal = self._auth(c, principal)
            saved = c.execute("SELECT * FROM claims WHERE worker_id=? AND request_id=?",
                              (principal.worker_id, request.request_id)).fetchone()
            if saved:
                if saved["principal_digest"] != digest(principal.to_dict()):
                    reject("ID_CONFLICT", "Claim request identity changed")
                return ClaimResponse.from_dict(json.loads(saved["response"])) if saved["response"] else None
            self._sweep(c, now)
            if c.execute("SELECT 1 FROM jobs WHERE worker_id=? AND status='running'", (principal.worker_id,)).fetchone():
                reject("LIMIT_EXCEEDED", "Worker already has an active analysis")
            result = None
            rows = c.execute("SELECT * FROM jobs WHERE producer_id=? AND producer_version=? AND role=? "
                             "AND status='queued' ORDER BY asof_ms DESC,job_id",
                             (principal.producer_id, principal.producer_version, principal.role)).fetchall()
            for row in rows:
                job = AnalysisJob.from_dict(json.loads(row["job"]))
                if job.role == "control":
                    control = c.execute("SELECT * FROM controls WHERE scope=?", (job.control_scope,)).fetchone()
                    if not control or (control["producer_id"], control["producer_version"], control["epoch"]) != (
                            job.producer_id, job.producer_version, job.control_epoch):
                        continue
                    if control["accepted_asof_ms"] is not None and job.asof_ms <= control["accepted_asof_ms"]:
                        continue
                    if self._busy_scope(c, job.control_scope):
                        continue
                if now - job.asof_ms > job.limits.max_input_age_ms:
                    continue
                job = transition_job(job, "claim", now)
                lease = Lease(LeaseRef(job.job_id, job.attempt, job.lease_epoch, secrets.token_urlsafe(32)),
                              principal.worker_id, now, min(now + lease_ms, job.deadline_ms))
                self._write_job(c, job, worker_id=principal.worker_id, lease=lease)
                result = ClaimResponse(job, lease)
                self.store.event(c, now, "job-claimed", {"job_id": job.job_id, "attempt": job.attempt,
                                                         "worker_id": principal.worker_id})
                break
            c.execute("INSERT INTO claims VALUES(?,?,?,?)", (principal.worker_id, request.request_id,
                      digest(principal.to_dict()), canonical(result.to_dict()) if result else None))
            return result

    def heartbeat(self, principal, ref, *, lease_ms):
        ref = LeaseRef.from_dict(ref.to_dict())
        decode(int, lease_ms)
        if lease_ms < 1:
            raise ValueError("Positive lease duration required")
        with self.store.transaction() as (c, now):
            principal = self._auth(c, principal)
            row, job = self._owned(c, ref.job_id, principal)
            lease = Lease.from_dict(json.loads(row["lease"])) if row["lease"] else None
            if lease is None:
                reject("LEASE_LOST", "No active lease")
            validate_lease(job, lease, ref, principal, now)
            self._control(c, job)
            lease = replace(lease, expires_ms=min(job.deadline_ms, max(lease.expires_ms, now + lease_ms)))
            self._write_job(c, job, worker_id=principal.worker_id, lease=lease)
            return lease

    def input_for(self, principal, ref):
        ref = LeaseRef.from_dict(ref.to_dict())
        with self.store.transaction() as (c, now):
            principal = self._auth(c, principal)
            row, job = self._owned(c, ref.job_id, principal)
            if row["lease"] is None:
                reject("LEASE_LOST", "No active lease")
            validate_lease(job, Lease.from_dict(json.loads(row["lease"])), ref, principal, now)
            self._control(c, job)
            return json.loads(row["manifest"])

    def submit(self, principal, submission, *, replay_status=False):
        submission = ForecastSubmission.from_dict(submission.to_dict())
        payload = submission.forecast_batch.to_dict()
        fingerprint = digest(payload)
        with self.store.transaction() as (c, now):
            principal = self._auth(c, principal)
            row, job = self._owned(c, submission.lease.job_id, principal)
            key = (principal.producer_id, principal.producer_version, submission.submission_id)
            alias = c.execute("SELECT * FROM submission_keys WHERE producer_id=? AND producer_version=? "
                              "AND submission_id=?", key).fetchone()
            if alias and (alias["job_id"], alias["payload_digest"]) != (job.job_id, fingerprint):
                reject("ID_CONFLICT", "Submission ID already binds another job or payload")
            saved = c.execute("SELECT * FROM submissions WHERE job_id=?", (job.job_id,)).fetchone()
            if saved:
                if (saved["batch_id"], saved["payload_digest"]) != (submission.forecast_batch.forecast_batch_id, fingerprint):
                    reject("ID_CONFLICT", "Job already accepted another immutable result")
                self._submission_key(c, key, job.job_id, fingerprint)
                receipt = SubmissionReceipt.from_dict(json.loads(saved["receipt"]))
                return (receipt, True) if replay_status else receipt
            if c.execute("SELECT 1 FROM submissions WHERE producer_id=? AND producer_version=? AND batch_id=?",
                         (job.producer_id, job.producer_version, submission.forecast_batch.forecast_batch_id)).fetchone():
                reject("ID_CONFLICT", "ForecastBatch ID already belongs to another job")
            if job.status in {"completed_without_forecast", "failed", "expired"}:
                reject("ID_CONFLICT", "Job already has a terminal outcome")
            if row["lease"] is None:
                reject("LEASE_LOST", "No active lease")
            control = self._control(c, job)
            manifest = json.loads(row["manifest"])
            requests = tuple(decode(DataRequest, r) for r in manifest["requests"])
            validated = validate_ingress(submission, job=job, lease=Lease.from_dict(json.loads(row["lease"])),
                                         principal=principal, context=manifest["context"], requests=requests,
                                         manifest_digest=row["manifest_digest"], received_ms=now,
                                         current_control_epoch=control["epoch"] if control else job.control_epoch,
                                         accepted_asof_ms=control["accepted_asof_ms"] if control else None)
            processing_id = "processing:" + digest({"job_id": job.job_id})
            receipt = SubmissionReceipt(job.job_id, submission.submission_id, validated.batch.forecast_batch_id,
                                        validated.payload_digest, now, processing_id)
            c.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?,?)", (job.job_id, job.producer_id,
                      job.producer_version, receipt.forecast_batch_id, fingerprint, canonical(payload),
                      canonical(receipt.to_dict())))
            self._submission_key(c, key, job.job_id, fingerprint)
            job = transition_job(job, "submit", now)
            self._write_job(c, job, worker_id=principal.worker_id, lease=Lease.from_dict(json.loads(row["lease"])))
            processing = ProcessingRecord(processing_id, job.job_id, validated.initial_processing_status,
                                          validated.reason_code, now, 1, None, None, None)
            c.execute("INSERT INTO processing VALUES(?,?,?,?,?,?)", (processing_id, job.job_id, job.control_scope,
                      job.role, processing.status, canonical(processing.to_dict())))
            c.execute("INSERT INTO outbox VALUES(?,?,0)", (processing_id,
                      "pending" if processing.status == "pending" else "done"))
            if job.role == "control":
                c.execute("UPDATE controls SET accepted_asof_ms=? WHERE scope=?", (job.asof_ms, job.control_scope))
            self.store.event(c, now, "forecast-accepted", receipt.to_dict())
            return (receipt, False) if replay_status else receipt

    @staticmethod
    def _submission_key(c, key, job_id, fingerprint):
        c.execute("INSERT OR IGNORE INTO submission_keys VALUES(?,?,?,?,?)", (*key, job_id, fingerprint))

    def complete(self, principal, request):
        request = CompleteRequest.from_dict(request.to_dict())
        fingerprint = digest({"job_id": request.lease.job_id, "outcome": request.outcome, "reason": request.reason})
        with self.store.transaction() as (c, now):
            principal = self._auth(c, principal)
            row, job = self._owned(c, request.lease.job_id, principal)
            old = c.execute("SELECT * FROM completions WHERE producer_id=? AND producer_version=? AND completion_id=?",
                            (principal.producer_id, principal.producer_version, request.completion_id)).fetchone()
            if old:
                if old["job_id"] != job.job_id or old["payload_digest"] != fingerprint:
                    reject("ID_CONFLICT", "Completion ID conflict")
                return CompleteResponse.from_dict(json.loads(old["response"]))
            if job.status != "running":
                reject("ID_CONFLICT", "Job already terminal or no longer running")
            validate_lease(job, Lease.from_dict(json.loads(row["lease"])), request.lease, principal, now)
            self._control(c, job)
            job = transition_job(job, "no_forecast" if request.outcome == "no_forecast" else "fail", now)
            self._write_job(c, job, worker_id=principal.worker_id, lease=Lease.from_dict(json.loads(row["lease"])))
            response = CompleteResponse(job.job_id, request.completion_id, job.status, now)
            c.execute("INSERT INTO completions VALUES(?,?,?,?,?,?)", (job.job_id, job.producer_id,
                      job.producer_version, request.completion_id, fingerprint, canonical(response.to_dict())))
            self.store.event(c, now, "analysis-completed", {"job_id": job.job_id, "status": job.status})
            return response

    def feedback(self, principal, job_id):
        with self.store.transaction() as (c, now):
            principal = self._auth(c, principal)
            row, job = self._owned(c, job_id, principal)
            result = c.execute("SELECT receipt FROM submissions WHERE job_id=?", (job_id,)).fetchone()
            processing = c.execute("SELECT record FROM processing WHERE job_id=?", (job_id,)).fetchone()
            revision = c.execute("SELECT coalesce(max(sequence),1) FROM audit").fetchone()[0]
            return FeedbackResponse(job_id, job.status, revision,
                                    SubmissionReceipt.from_dict(json.loads(result[0])) if result else None,
                                    ProcessingRecord.from_dict(json.loads(processing[0])) if processing else None)

    def inspect(self, job_id):
        """Trusted operator view for recovery/tests; not an Agent endpoint."""
        with self.store.connection() as c:
            return self._job(c, job_id)[1]
