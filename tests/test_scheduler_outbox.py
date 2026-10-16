import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from runtime_support import claim, enqueue, job, manifest, setup_runtime, submission

from perpetual_alpha.runtime.ingress import IngressRejected
from perpetual_alpha.runtime.outbox import Outbox
from perpetual_alpha.runtime.scheduler import Schedule, Scheduler
from perpetual_alpha.transport.messages import CompleteRequest


class SchedulerOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime.sqlite3"
        self.clock, self.store, self.queue, self.principal = setup_runtime(self.path)
        self.scheduler = Scheduler(self.queue)
        self.schedule = Schedule("one-minute", "dummy-agent", "test-0.1", "paper", 1, "control",
                                 ("par:BTC",), 10000, 120000, job().limits)

    def tearDown(self):
        self.temp.cleanup()

    def factory(self, context_id, asof):
        return manifest(replace(job(asof=asof, created=asof), context_id=context_id))

    def accepted(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        self.clock.now = 108001
        receipt = self.queue.submit(self.principal, submission(claimed))
        return receipt

    def test_tick_deduplicates_across_scheduler_restarts(self):
        first = self.scheduler.tick(self.schedule, self.factory)
        self.assertEqual(first.asof_ms, 100000)
        with patch.object(self, "factory", side_effect=AssertionError("Do not rebuild frozen job")):
            self.assertIsNone(Scheduler(self.queue).tick(self.schedule, self.factory))
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_busy_ticks_coalesce_before_materialization_and_never_backfill(self):
        first = self.scheduler.tick(self.schedule, self.factory)
        claimed = claim(self.queue, self.principal)
        self.clock.now = 110000
        self.assertIsNone(self.scheduler.tick(self.schedule, self.factory))
        self.clock.now = 120000
        self.assertIsNone(self.scheduler.tick(self.schedule, self.factory))
        self.queue.complete(self.principal, CompleteRequest("done", claimed.lease.ref, "no_forecast", "No signal"))
        latest = self.scheduler.tick(self.schedule, self.factory)
        self.assertEqual(latest.asof_ms, 120000)
        self.assertEqual(self.queue.inspect(first.job_id).asof_ms, 100000)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)

    def test_missed_ticks_skip_expired_jobs_and_create_only_latest_tick(self):
        first = self.scheduler.tick(self.schedule, self.factory)
        self.clock.now = 250001
        latest = self.scheduler.tick(self.schedule, self.factory)
        self.assertEqual(latest.asof_ms, 250000)
        self.assertEqual(self.queue.inspect(first.job_id).status, "expired")
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)

    def test_schedule_configuration_change_requires_new_identity_or_epoch(self):
        self.scheduler.tick(self.schedule, self.factory)
        with self.assertRaises(IngressRejected):
            self.scheduler.tick(replace(self.schedule, interval_ms=20000), self.factory)

    def test_control_rotation_does_not_mutate_or_get_blocked_by_unclaimed_old_epoch_job(self):
        old = self.scheduler.tick(self.schedule, self.factory)
        self.queue.configure_control("paper", "dummy-agent", "test-0.1", 2)
        new = self.scheduler.tick(replace(self.schedule, control_epoch=2), self.factory)
        self.assertNotEqual(old.job_id, new.job_id)
        self.assertEqual(self.queue.inspect(old.job_id).status, "queued")
        self.assertEqual(claim(self.queue, self.principal).job.control_epoch, 2)

    def test_provider_io_is_outside_write_lock_and_tick_change_discards_uncommitted_context(self):
        def provider(context_id, asof):
            # This would deadlock/fail if the provider ran inside the scheduler's write transaction.
            with self.store.transaction() as (c, now):
                self.assertFalse(c.execute("SELECT 1 FROM jobs").fetchone())
            self.clock.now += 10000
            return self.factory(context_id, asof)

        self.assertIsNone(self.scheduler.tick(self.schedule, provider))
        item = self.scheduler.tick(self.schedule, self.factory)
        self.assertEqual(item.asof_ms, 110000)

    def test_provider_failure_does_not_leave_half_created_job_and_same_tick_can_retry(self):
        with self.assertRaises(RuntimeError):
            self.scheduler.tick(self.schedule, lambda *_: (_ for _ in ()).throw(RuntimeError("provider down")))
        self.assertIsNotNone(self.scheduler.tick(self.schedule, self.factory))

    def test_pending_processing_blocks_next_control_analysis(self):
        receipt = self.accepted()
        self.clock.now = 110000
        self.assertIsNone(self.scheduler.tick(self.schedule, self.factory))
        outbox = Outbox(self.store)
        delivery = outbox.begin()
        self.assertEqual(delivery.processing.processing_id, receipt.processing_id)
        outbox.finish(receipt.processing_id, delivery.delivery_epoch, "complete")
        self.assertIsNotNone(self.scheduler.tick(self.schedule, self.factory))

    def test_older_queued_job_cannot_be_claimed_or_block_new_tick_after_newer_result(self):
        enqueue(self.queue, job("old"))
        self.clock.now = 100001
        enqueue(self.queue, job("new", asof=100001, created=100001))
        claimed = claim(self.queue, self.principal)
        self.assertEqual(claimed.job.job_id, "job-new")
        self.clock.now = 108001
        receipt = self.queue.submit(self.principal, submission(claimed))
        outbox = Outbox(self.store)
        delivery = outbox.begin()
        outbox.finish(receipt.processing_id, delivery.delivery_epoch, "complete")
        self.assertIsNone(claim(self.queue, self.principal, "stale-queued"))
        self.clock.now = 110000
        self.assertIsNotNone(self.scheduler.tick(self.schedule, self.factory))

    def test_expired_prediction_blocks_processing_without_requeueing_analysis(self):
        receipt = self.accepted()
        self.clock.now = 190000
        self.assertIsNone(Outbox(self.store).begin())
        feedback = self.queue.feedback(self.principal, "job-one")
        self.assertEqual(feedback.processing.reason_code, "EXPIRED")
        self.assertEqual(feedback.job_status, "submitted")
        self.assertEqual(feedback.receipt, receipt)

    def test_control_rotation_or_newer_acceptance_blocks_already_queued_old_result(self):
        receipt = self.accepted()
        self.queue.configure_control("paper", "dummy-agent", "test-0.1", 2)
        self.assertIsNone(Outbox(self.store).begin())
        self.assertEqual(self.queue.feedback(self.principal, "job-one").processing.reason_code, "STALE_RESULT")
        self.assertEqual(self.queue.feedback(self.principal, "job-one").receipt, receipt)

    def test_consumer_start_rechecks_database_watermarks_and_cannot_trust_old_preflight(self):
        self.accepted()
        with self.store.connection() as c:
            c.execute("UPDATE controls SET accepted_asof_ms=110000")
        self.assertIsNone(Outbox(self.store).begin())
        self.assertEqual(self.queue.feedback(self.principal, "job-one").processing.status, "blocked")

    def test_delivery_epoch_prevents_stale_completion_after_recovery(self):
        receipt = self.accepted()
        outbox = Outbox(self.store)
        first = outbox.begin()
        self.assertEqual(outbox.recover(), 1)
        second = outbox.begin()
        self.assertEqual(first.processing.processing_id, second.processing.processing_id)
        with self.assertRaises(IngressRejected):
            outbox.finish(receipt.processing_id, first.delivery_epoch, "complete")
        final = outbox.finish(receipt.processing_id, second.delivery_epoch, "complete", decision_id="stable-decision")
        self.assertEqual(final.decision_id, "stable-decision")
        self.assertIsNone(outbox.begin())
        with self.assertRaises(IngressRejected):
            outbox.finish(receipt.processing_id, second.delivery_epoch, "complete")

    def test_consumed_watermark_only_advances_for_valid_completion(self):
        receipt = self.accepted()
        outbox = Outbox(self.store)
        delivery = outbox.begin()
        with self.store.connection() as c:
            self.assertIsNone(c.execute("SELECT consumed_asof_ms FROM controls").fetchone()[0])
        outbox.finish(receipt.processing_id, delivery.delivery_epoch, "complete")
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT consumed_asof_ms FROM controls").fetchone()[0], 100000)

    def test_configuration_change_before_finish_cannot_mark_old_processing_successful(self):
        receipt = self.accepted()
        outbox = Outbox(self.store)
        delivery = outbox.begin()
        self.queue.configure_control("paper", "dummy-agent", "test-0.1", 2)
        final = outbox.finish(receipt.processing_id, delivery.delivery_epoch, "complete")
        self.assertEqual((final.status, final.reason_code), ("blocked", "STALE_RESULT"))
        with self.store.connection() as c:
            self.assertIsNone(c.execute("SELECT consumed_asof_ms FROM controls").fetchone()[0])

    def test_sql_failure_after_result_insert_rolls_back_terminal_outbox_and_watermark(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        self.clock.now = 108001
        with self.store.connection() as c:
            c.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON outbox "
                      "BEGIN SELECT RAISE(ABORT,'injected write failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.queue.submit(self.principal, submission(claimed))
        self.assertEqual(self.queue.inspect("job-one").status, "running")
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM submissions").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM processing").fetchone()[0], 0)
            self.assertIsNone(c.execute("SELECT accepted_asof_ms FROM controls").fetchone()[0])
