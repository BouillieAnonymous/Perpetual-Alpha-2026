import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from runtime_support import claim, enqueue, job, manifest, setup_runtime, submission

from perpetual_alpha.forecasting import ForecastUnavailable
from perpetual_alpha.runtime.ingress import IngressRejected
from perpetual_alpha.runtime.jobs import JobQueue
from perpetual_alpha.runtime.outbox import Outbox
from perpetual_alpha.runtime.store import RuntimeStore
from perpetual_alpha.transport.messages import CompleteRequest


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime.sqlite3"
        self.clock, self.store, self.queue, self.principal = setup_runtime(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def count(self, table):
        with self.store.connection() as c:
            return c.execute("SELECT count(*) FROM " + table).fetchone()[0]

    def accept(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        self.clock.now = 108001
        sent = submission(claimed)
        receipt = self.queue.submit(self.principal, sent)
        return claimed, sent, receipt

    def test_runtime_is_versioned_wal_full_sync_foreign_keys_and_refuses_other_dbs(self):
        with self.store.connection() as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(c.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        other = Path(self.temp.name) / "collector.sqlite3"
        with sqlite3.connect(other) as c:
            c.execute("CREATE TABLE snapshots(x)")
        c.close()
        with self.assertRaises(ValueError):
            RuntimeStore(other)
        with self.store.connection() as c:
            c.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError):
            RuntimeStore(self.path)

    def test_job_and_context_immutable_admission_and_backpressure(self):
        first = enqueue(self.queue)
        self.assertEqual(enqueue(self.queue), first)
        with self.assertRaises(IngressRejected):
            enqueue(self.queue, replace(first, symbols=("par:ETH",)))
        with self.assertRaises(IngressRejected):
            enqueue(self.queue, replace(first, job_id="other-job"))
        with self.assertRaises(IngressRejected) as error:
            enqueue(JobQueue(self.store, max_active_jobs=1), job("two"))
        self.assertEqual(error.exception.code, "LIMIT_EXCEEDED")

    def test_claim_replay_survives_reopen_and_does_not_take_another_job(self):
        enqueue(self.queue)
        first = claim(self.queue, self.principal)
        reopened = JobQueue(RuntimeStore(self.path, clock=self.clock))
        replay = claim(reopened, self.principal)
        self.assertEqual(replay, first)
        self.assertEqual(reopened.inspect(first.job.job_id).attempt, 1)
        self.assertEqual(self.count("claims"), 1)

    def test_empty_claim_id_remains_empty_new_poll_requires_new_id(self):
        self.assertIsNone(claim(self.queue, self.principal))
        enqueue(self.queue)
        self.assertIsNone(claim(self.queue, self.principal))
        self.assertIsNotNone(claim(self.queue, self.principal, "fresh-poll"))

    def test_worker_and_control_scope_serialization_are_server_enforced(self):
        enqueue(self.queue, job("a"))
        enqueue(self.queue, job("b"))
        second = replace(self.principal, worker_id="worker-b")
        self.queue.configure_worker(second)
        claim(self.queue, self.principal)
        with self.assertRaises(IngressRejected):
            claim(self.queue, self.principal, "another-request")
        self.assertIsNone(claim(self.queue, second))
        with self.store.connection() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE jobs SET status='running',worker_id='worker-b' WHERE status='queued'")

    def test_different_control_scope_can_run_independently(self):
        self.queue.configure_control("other-account", "dummy-agent", "test-0.1", 1)
        enqueue(self.queue, job("a"))
        enqueue(self.queue, job("b", scope="other-account"))
        second = replace(self.principal, worker_id="worker-b")
        self.queue.configure_worker(second)
        a = claim(self.queue, self.principal)
        b = claim(self.queue, second)
        self.assertNotEqual(a.job.control_scope, b.job.control_scope)

    def test_heartbeat_clamped_to_deadline_does_not_reset_claim_or_inference(self):
        enqueue(self.queue)
        lease = claim(self.queue, self.principal).lease
        self.clock.now = 110000
        renewed = self.queue.heartbeat(self.principal, lease.ref, lease_ms=200000)
        self.assertEqual(renewed.claimed_ms, 100000)
        self.assertEqual(renewed.expires_ms, 220000)
        self.clock.now = 120001
        sent = submission(claim(self.queue, self.principal))
        with self.assertRaises(ForecastUnavailable) as error:
            self.queue.submit(self.principal, sent)
        self.assertEqual(error.exception.reason_code, "INFERENCE_TIMEOUT")

    def test_restart_preserves_live_lease_then_requeues_expired_and_fences_old_worker(self):
        enqueue(self.queue)
        old = claim(self.queue, self.principal)
        self.assertEqual(self.queue.recover_jobs()["requeued"], 0)
        self.clock.now = 130000
        self.assertEqual(self.queue.recover_jobs()["requeued"], 1)
        new = claim(self.queue, self.principal, "claim-two")
        self.assertEqual((new.job.attempt, new.job.lease_epoch), (2, 2))
        self.assertNotEqual(old.lease.ref.lease_token, new.lease.ref.lease_token)
        with self.assertRaises(IngressRejected):
            self.queue.heartbeat(self.principal, old.lease.ref, lease_ms=30000)
        with self.assertRaises(IngressRejected):
            self.queue.submit(self.principal, submission(old))
        self.clock.now = 160000
        self.assertEqual(self.queue.recover_jobs()["failed"], 1)

    def test_hard_deadline_expires_and_never_requeues(self):
        enqueue(self.queue)
        claim(self.queue, self.principal)
        self.clock.now = 220000
        self.assertEqual(self.queue.recover_jobs()["expired"], 1)
        self.assertEqual(self.queue.inspect("job-one").status, "expired")
        self.assertIsNone(claim(self.queue, self.principal, "after-deadline"))

    def test_accept_commits_result_terminal_processing_outbox_and_watermark_together(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("No HTTP")), \
                patch("perpetual_alpha.execution.Exchange", side_effect=AssertionError("No signing")):
            claimed, sent, receipt = self.accept()
        self.assertEqual(self.queue.inspect(claimed.job.job_id).status, "submitted")
        for table in ("submissions", "submission_keys", "processing", "outbox"):
            self.assertEqual(self.count(table), 1)
        feedback = self.queue.feedback(self.principal, claimed.job.job_id)
        self.assertEqual(feedback.receipt, receipt)
        self.assertEqual(feedback.processing.status, "pending")
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT accepted_asof_ms FROM controls").fetchone()[0], 100000)

    def test_same_or_new_submission_id_replays_original_after_lease_and_forecast_expire(self):
        claimed, sent, receipt = self.accept()
        self.clock.now = 230000
        for submission_id in (sent.submission_id, "new-alias"):
            self.assertEqual(self.queue.submit(self.principal, replace(sent, submission_id=submission_id)), receipt)
        self.assertEqual(self.count("outbox"), 1)
        self.assertEqual(self.count("submission_keys"), 2)
        with self.assertRaises(IngressRejected) as error:
            self.queue.submit(self.principal, replace(sent, forecast_batch=replace(sent.forecast_batch, items=())))
        self.assertEqual(error.exception.code, "ID_CONFLICT")

    def test_new_id_different_batch_cannot_replace_job_result(self):
        claimed, sent, receipt = self.accept()
        with self.assertRaises(IngressRejected):
            self.queue.submit(self.principal, submission(claimed, submission_id="new", batch_id="different"))
        self.assertEqual(self.queue.feedback(self.principal, claimed.job.job_id).receipt, receipt)

    def test_batch_and_submission_ids_cannot_be_reused_across_jobs(self):
        claimed, sent, receipt = self.accept()
        outbox = Outbox(self.store)
        delivery = outbox.begin()
        outbox.finish(delivery.processing.processing_id, delivery.delivery_epoch, "complete")
        self.clock.now = 110000
        enqueue(self.queue, job("two", asof=110000, created=110000))
        next_claim = claim(self.queue, self.principal, "claim-two")
        self.clock.now = 118001
        for kwargs in ({"submission_id": "different", "batch_id": "batch-one"},
                       {"submission_id": "submit-one", "batch_id": "different"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(IngressRejected):
                self.queue.submit(self.principal, submission(next_claim, created=118000, **kwargs))
        self.assertEqual(self.queue.inspect("job-two").status, "running")

    def test_revocation_or_other_worker_forbids_even_receipt_replays(self):
        claimed, sent, receipt = self.accept()
        other = replace(self.principal, worker_id="worker-b")
        self.queue.configure_worker(other)
        with self.assertRaises(IngressRejected):
            self.queue.submit(other, sent)
        self.queue.configure_worker(self.principal, enabled=False)
        for action in (lambda: self.queue.submit(self.principal, sent),
                       lambda: self.queue.feedback(self.principal, claimed.job.job_id)):
            with self.assertRaises(IngressRejected):
                action()

    def test_control_rotation_and_latest_db_watermark_reject_preflight_stale_result(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        self.clock.now = 108001
        with self.store.connection() as c:
            c.execute("UPDATE controls SET accepted_asof_ms=110000")
        with self.assertRaises(IngressRejected) as error:
            self.queue.submit(self.principal, submission(claimed))
        self.assertEqual(error.exception.code, "STALE_RESULT")
        self.queue.configure_control("paper", "dummy-agent", "test-0.1", 2)
        with self.assertRaises(IngressRejected):
            self.queue.submit(self.principal, submission(claimed))
        self.assertEqual(self.count("submissions"), 0)
        with self.assertRaises(IngressRejected):
            self.queue.configure_control("paper", "other", "v", 2)

    def test_manifest_tamper_rejected_inside_accept_transaction(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        self.clock.now = 108001
        with self.store.connection() as c:
            data = json.loads(c.execute("SELECT manifest FROM jobs").fetchone()[0])
            data["context"]["datasets"]["btc-quotes"]["records"][0]["data"]["bid"] = "1"
            c.execute("UPDATE jobs SET manifest=?", (json.dumps(data),))
        with self.assertRaises(IngressRejected):
            self.queue.submit(self.principal, submission(claimed))
        self.assertEqual(self.count("outbox"), 0)

    def test_no_forecast_completion_is_terminal_idempotent_and_not_an_outbox_event(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        request = CompleteRequest("complete-one", claimed.lease.ref, "no_forecast", "Insufficient evidence")
        response = self.queue.complete(self.principal, request)
        self.clock.now = 230000
        self.assertEqual(self.queue.complete(self.principal, request), response)
        with self.assertRaises(IngressRejected):
            self.queue.complete(self.principal, replace(request, reason="different"))
        with self.assertRaises(IngressRejected):
            self.queue.submit(self.principal, submission(claimed))
        self.assertEqual(self.count("outbox"), 0)

    def test_shadow_and_error_results_are_persisted_without_deliverable_outbox_work(self):
        shadow = replace(self.principal, worker_id="shadow", role="shadow")
        self.queue.configure_worker(shadow)
        enqueue(self.queue, job("shadow", role="shadow"))
        claimed = claim(self.queue, shadow)
        self.clock.now = 108001
        self.queue.submit(shadow, submission(claimed))
        self.assertEqual(self.queue.feedback(shadow, claimed.job.job_id).processing.reason_code, "SHADOW_ONLY")
        self.assertIsNone(Outbox(self.store).begin())
        self.clock.now = 110000
        enqueue(self.queue, job("error", asof=110000, created=110000))
        claimed = claim(self.queue, self.principal, "error")
        sent = submission(claimed, submission_id="error", batch_id="error", created=110000)
        error_item = replace(sent.forecast_batch.items[0], status="error", direction=None, evidence_refs=())
        self.queue.submit(self.principal, replace(sent, forecast_batch=replace(sent.forecast_batch, items=(error_item,))))
        self.assertEqual(self.queue.feedback(self.principal, claimed.job.job_id).processing.status, "blocked")
        self.assertIsNone(Outbox(self.store).begin())

    def test_bad_forecast_leaves_running_job_and_no_partial_result(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        self.clock.now = 108001
        sent = submission(claimed)
        bad = replace(sent.forecast_batch.items[0], evidence_refs=("unreturned",))
        with self.assertRaises(ForecastUnavailable):
            self.queue.submit(self.principal, replace(sent, forecast_batch=replace(sent.forecast_batch, items=(bad,))))
        self.assertEqual(self.queue.inspect(claimed.job.job_id).status, "running")
        self.assertEqual(self.count("submissions"), 0)
        self.queue.submit(self.principal, sent)

    def test_clock_rollback_and_audit_does_not_leak_lease_or_fake_simulation(self):
        self.accept()
        text = json.dumps(self.store.audit())
        self.assertNotIn("lease_token", text)
        self.assertNotIn('"simulated"', text)
        self.clock.now = 100000
        with self.assertRaises(ValueError):
            self.queue.recover_jobs()

    def test_job_creation_retry_after_deadline_returns_original_instead_of_creating_another(self):
        original = enqueue(self.queue)
        self.clock.now = 230000
        result = enqueue(self.queue)
        self.assertEqual(result.job_id, original.job_id)
        self.assertEqual(result.status, "expired")
        self.assertEqual(self.count("jobs"), 1)

    def test_manifest_admission_preserves_real_required_state_and_allows_explicit_missing_data(self):
        item = job()
        context, requests = manifest(item)
        context["datasets"]["btc-quotes"]["required"] = False
        with self.assertRaises(IngressRejected):
            self.queue.enqueue(item, context, requests)
        context["datasets"]["btc-quotes"].update(required=True, status="missing", records=[])
        self.queue.enqueue(item, context, requests)
        claimed = claim(self.queue, self.principal)
        actual = self.queue.input_for(self.principal, claimed.lease.ref)
        self.assertTrue(actual["context"]["datasets"]["btc-quotes"]["required"])
        self.clock.now = 108001
        with self.assertRaises(ForecastUnavailable):
            self.queue.submit(self.principal, submission(claimed))
        self.queue.complete(self.principal, CompleteRequest("missing", claimed.lease.ref, "no_forecast", "Missing data"))

    def test_bounded_sqlite_busy_failure_does_not_consume_claim_id(self):
        enqueue(self.queue)
        short_wait = JobQueue(RuntimeStore(self.path, clock=self.clock, timeout_ms=25))
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    claim(short_wait, self.principal)
            finally:
                c.rollback()
        self.assertEqual(self.count("claims"), 0)
        self.assertIsNotNone(claim(short_wait, self.principal))

    def test_failed_migration_rolls_back_and_can_retry_without_partial_tables(self):
        fresh = Path(self.temp.name) / "failed-migration.sqlite3"
        with patch("pathlib.Path.read_text", return_value="CREATE TABLE partial(x); NOT SQL;"):
            with self.assertRaises(sqlite3.OperationalError):
                RuntimeStore(fresh)
        restored = RuntimeStore(fresh)
        with restored.connection() as c:
            self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='partial'").fetchone())
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_consistent_sqlite_backup_can_restore_accepted_result_and_receipt_keys(self):
        claimed, sent, receipt = self.accept()
        backup = Path(self.temp.name) / "backup.sqlite3"
        with self.store.connection() as source:
            destination = sqlite3.connect(backup)
            try:
                source.backup(destination)
            finally:
                destination.close()
        restored = JobQueue(RuntimeStore(backup, clock=self.clock))
        self.assertEqual(restored.submit(self.principal, sent), receipt)
        self.assertEqual(restored.inspect(claimed.job.job_id).status, "submitted")

    def test_immutable_input_view_is_owned_and_cannot_mutate_stored_manifest(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        view = self.queue.input_for(self.principal, claimed.lease.ref)
        view["context"]["asof_ms"] = 0
        self.assertEqual(self.queue.input_for(self.principal, claimed.lease.ref)["context"]["asof_ms"], 100000)

    def test_trusted_acceptance_clock_is_read_after_waiting_for_write_lock(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        sent = submission(claimed)
        clock_read = threading.Event()
        started = threading.Event()
        results = []
        self.store.clock = lambda: (clock_read.set(), self.clock.now)[1]

        def submit_after_lock():
            started.set()
            try:
                self.queue.submit(self.principal, sent)
                results.append("accepted")
            except IngressRejected as error:
                results.append(error.code)

        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            thread = threading.Thread(target=submit_after_lock)
            thread.start()
            try:
                self.assertTrue(started.wait(5))
                self.assertFalse(clock_read.wait(0.1), "Clock sampled before SQLite write lock")
                self.clock.now = 130000
            finally:
                c.commit()
                thread.join(10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["LEASE_LOST"])
