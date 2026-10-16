"""A scheduler tick, not a daemon. Collapse missed triggers BEFORE creating immutable jobs."""
from dataclasses import asdict, dataclass

from ..journal import digest
from .contracts import AnalysisJob, JobLimits, Role, WireContract, decode
from .jobs import reject


@dataclass(frozen=True)
class Schedule(WireContract):
    schedule_id: str
    producer_id: str
    producer_version: str
    control_scope: str
    control_epoch: int
    role: Role
    symbols: tuple[str, ...]
    interval_ms: int
    deadline_after_tick_ms: int
    limits: JobLimits

    def check(self):
        if not self.interval_ms or not self.deadline_after_tick_ms or not self.control_epoch:
            raise ValueError("Positive schedule times and epoch required")
        # Reuse the job allowlist validator without inventing a second symbol policy.
        AnalysisJob("validate", "validate", self.producer_id, self.producer_version, self.control_scope,
                    self.control_epoch, self.role, self.symbols, 0, 0, 1, "queued", 0, 0, self.limits)

    @property
    def group(self):
        return digest({"schedule_id": self.schedule_id, "producer_id": self.producer_id,
                       "producer_version": self.producer_version, "control_epoch": self.control_epoch})


class Scheduler:
    def __init__(self, queue):
        self.queue, self.store = queue, queue.store

    def tick(self, schedule, context_factory):
        """factory(context_id, asof_ms) -> (context, tuple[DataRequest]); all provider I/O outside DB lock."""
        schedule = Schedule.from_dict(schedule.to_dict())
        now = self.store.clock()
        decode(int, now)
        tick = now // schedule.interval_ms * schedule.interval_ms
        job_id = "job:" + digest({"schedule": schedule.group, "tick": tick})
        context_id = "context:" + job_id
        # Avoid even building context for already handled ticks or while the scope is busy.
        with self.store.transaction() as (c, locked_now):
            self.queue._sweep(c, locked_now)
            latest = locked_now // schedule.interval_ms * schedule.interval_ms
            row = c.execute("SELECT * FROM schedules WHERE schedule_group=?", (schedule.group,)).fetchone()
            config_digest = digest(asdict(schedule))
            if row and row["config_digest"] != config_digest:
                reject("ID_CONFLICT", "Change schedule ID or control epoch when changing schedule configuration")
            c.execute("INSERT INTO schedules VALUES(?,?,?,NULL) ON CONFLICT(schedule_group) DO UPDATE SET "
                      "pending_tick=max(pending_tick,excluded.pending_tick)", (schedule.group, config_digest, latest))
            control = self.queue._control(c, schedule)
            if (schedule.role == "control" and control["accepted_asof_ms"] is not None
                    and latest <= control["accepted_asof_ms"]):
                return None
            if row and row["last_job_tick"] is not None and latest <= row["last_job_tick"]:
                return None
            busy = self.queue._busy_scope(c, schedule.control_scope, include_queued=True) if schedule.role == "control" else (
                c.execute("SELECT 1 FROM jobs WHERE schedule_group=? AND status IN ('queued','running')",
                          (schedule.group,)).fetchone())
            if busy or latest != tick or locked_now >= latest + schedule.deadline_after_tick_ms:
                return None
        context, requests = context_factory(context_id, tick)
        with self.store.transaction() as (c, locked_now):
            self.queue._sweep(c, locked_now)
            latest = locked_now // schedule.interval_ms * schedule.interval_ms
            row = c.execute("SELECT * FROM schedules WHERE schedule_group=?", (schedule.group,)).fetchone()
            if (latest != tick or locked_now >= tick + schedule.deadline_after_tick_ms
                    or (row["last_job_tick"] is not None and row["last_job_tick"] >= tick)):
                return None
            control = self.queue._control(c, schedule)
            if (schedule.role == "control" and control["accepted_asof_ms"] is not None
                    and tick <= control["accepted_asof_ms"]):
                return None
            if schedule.role == "control" and self.queue._busy_scope(c, schedule.control_scope, include_queued=True):
                return None
            if schedule.role == "shadow" and c.execute(
                    "SELECT 1 FROM jobs WHERE schedule_group=? AND status IN ('queued','running')",
                    (schedule.group,)).fetchone():
                return None
            job = AnalysisJob(job_id, context_id, schedule.producer_id, schedule.producer_version,
                              schedule.control_scope, schedule.control_epoch, schedule.role, schedule.symbols,
                              tick, locked_now, tick + schedule.deadline_after_tick_ms, "queued", 0, 0, schedule.limits)
            self.queue._insert(c, locked_now, job, context, requests, schedule_group=schedule.group, schedule_tick=tick)
            c.execute("UPDATE schedules SET last_job_tick=?,pending_tick=? WHERE schedule_group=?",
                      (tick, tick, schedule.group))
            return job
