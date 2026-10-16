"""Durable delivery reservations and fencing. Does not call Portfolio, Risk or Execution."""
import json
from dataclasses import dataclass, replace

from ..contracts import ForecastBatch
from ..journal import canonical
from .contracts import ProcessingRecord, decode, transition_processing
from .jobs import JobQueue, reject


@dataclass(frozen=True)
class Delivery:
    processing: ProcessingRecord
    delivery_epoch: int
    batch: ForecastBatch
    manifest: dict


class Outbox:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _invalid(c, job, batch, now):
        control = c.execute("SELECT * FROM controls WHERE scope=?", (job.control_scope,)).fetchone()
        if job.role != "control":
            return "SHADOW_ONLY"
        if not control or (control["producer_id"], control["producer_version"], control["epoch"]) != (
                job.producer_id, job.producer_version, job.control_epoch):
            return "STALE_RESULT"
        if ((control["accepted_asof_ms"] is not None and job.asof_ms < control["accepted_asof_ms"])
                or (control["consumed_asof_ms"] is not None and job.asof_ms <= control["consumed_asof_ms"])):
            return "STALE_RESULT"
        if any(item.expires_ms <= now for item in batch.items):
            return "EXPIRED"
        if not 0 <= now - job.asof_ms <= job.limits.max_input_age_ms:
            return "STALE_INPUT"
        return None

    @staticmethod
    def _write(c, record, outbox_status, epoch):
        c.execute("UPDATE processing SET status=?,record=? WHERE processing_id=?",
                  (record.status, canonical(record.to_dict()), record.processing_id))
        c.execute("UPDATE outbox SET status=?,delivery_epoch=? WHERE processing_id=?",
                  (outbox_status, epoch, record.processing_id))

    def begin(self):
        with self.store.transaction() as (c, now):
            rows = c.execute("SELECT p.*,o.delivery_epoch,s.payload FROM outbox o JOIN processing p "
                             "USING(processing_id) JOIN submissions s USING(job_id) JOIN jobs j USING(job_id) "
                             "WHERE o.status='pending' ORDER BY j.asof_ms DESC,p.processing_id").fetchall()
            for row in rows:
                job_row, job = JobQueue._job(c, row["job_id"])
                batch = ForecastBatch.from_dict(json.loads(row["payload"]))
                record = ProcessingRecord.from_dict(json.loads(row["record"]))
                invalid = self._invalid(c, job, batch, now)
                if not invalid and c.execute("SELECT 1 FROM processing WHERE scope=? AND role='control' "
                                             "AND status='running'", (job.control_scope,)).fetchone():
                    continue
                record = transition_processing(record, "start", now)
                epoch = row["delivery_epoch"] + 1
                if invalid:
                    record = transition_processing(record, "block", now, reason_code=invalid)
                    self._write(c, record, "done", epoch)
                    self.store.event(c, now, "processing-blocked", {"processing_id": record.processing_id,
                                                                    "reason_code": invalid})
                    continue
                self._write(c, record, "running", epoch)
                self.store.event(c, now, "processing-started", {"processing_id": record.processing_id,
                                                               "delivery_epoch": epoch})
                return Delivery(record, epoch, batch, json.loads(job_row["manifest"]))
            return None

    def finish(self, processing_id, delivery_epoch, event, *, reason_code="OK",
               decision_id=None, verdict_id=None, report_id=None):
        decode(str, processing_id)
        decode(int, delivery_epoch)
        if event not in {"complete", "block", "fail"}:
            raise ValueError("Explicit terminal processing event required")
        with self.store.transaction() as (c, now):
            row = c.execute("SELECT p.*,o.status AS delivery_status,o.delivery_epoch,s.payload FROM processing p "
                            "JOIN outbox o USING(processing_id) JOIN submissions s USING(job_id) "
                            "WHERE processing_id=?", (processing_id,)).fetchone()
            if not row or row["delivery_status"] != "running" or row["delivery_epoch"] != delivery_epoch:
                reject("LEASE_LOST", "Stale or already-finished delivery reservation")
            job = JobQueue._job(c, row["job_id"])[1]
            record = ProcessingRecord.from_dict(json.loads(row["record"]))
            if event == "complete":
                invalid = self._invalid(c, job, ForecastBatch.from_dict(json.loads(row["payload"])), now)
                if invalid:
                    event, reason_code = "block", invalid
            record = transition_processing(record, event, now, reason_code=reason_code)
            record = replace(record, decision_id=decision_id, verdict_id=verdict_id, report_id=report_id)
            self._write(c, record, "done", delivery_epoch)
            if event == "complete" and job.role == "control":
                c.execute("UPDATE controls SET consumed_asof_ms=? WHERE scope=?", (job.asof_ms, job.control_scope))
            self.store.event(c, now, "processing-" + record.status, {"processing_id": processing_id,
                                                                   "reason_code": record.reason_code})
            return record

    def recover(self):
        """Startup only AFTER the former Core/consumer has stopped. Never infer an external fill."""
        with self.store.transaction() as (c, now):
            rows = c.execute("SELECT p.record,o.delivery_epoch FROM outbox o JOIN processing p "
                             "USING(processing_id) WHERE o.status='running'").fetchall()
            for row in rows:
                record = transition_processing(ProcessingRecord.from_dict(json.loads(row["record"])), "recover", now)
                self._write(c, record, "pending", row["delivery_epoch"] + 1)
                self.store.event(c, now, "processing-recovered", {"processing_id": record.processing_id})
            return len(rows)
