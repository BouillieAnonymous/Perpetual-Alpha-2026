"""Independent processes contend on a real WAL file; hard exits exercise commit/reply windows."""
import multiprocessing
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from runtime_support import claim, enqueue, setup_runtime, submission

from perpetual_alpha.runtime.contracts import LeaseRef
from perpetual_alpha.runtime.ingress import IngressRejected, Principal
from perpetual_alpha.runtime.jobs import JobQueue
from perpetual_alpha.runtime.outbox import Outbox
from perpetual_alpha.runtime.scheduler import Schedule, Scheduler
from perpetual_alpha.runtime.store import RuntimeStore
from perpetual_alpha.transport.messages import ClaimRequest, CompleteRequest, ForecastSubmission


def contend(path, operation, body, identity, now, gate, channel):
    try:
        if operation != "initialize":
            store = RuntimeStore(path, clock=lambda: now)
        channel.send("ready")
        if not gate.wait(15):
            raise TimeoutError("Test start gate timed out")
        if operation == "initialize":
            store = RuntimeStore(path, clock=lambda: now)
            channel.send({"result": "initialized"})
            return
        queue = JobQueue(store)
        principal = Principal.from_dict(identity)
        if operation == "claim":
            result = queue.claim(principal, ClaimRequest.from_dict(body), lease_ms=30000)
        elif operation == "submit":
            result = queue.submit(principal, ForecastSubmission.from_dict(body))
        elif operation == "heartbeat":
            result = queue.heartbeat(principal, LeaseRef.from_dict(body), lease_ms=30000)
        elif operation == "schedule":
            from runtime_support import job, manifest

            def factory(context_id, asof):
                return manifest(replace(job(asof=asof, created=asof), context_id=context_id))

            result = Scheduler(queue).tick(Schedule.from_dict(body), factory)
        elif operation == "outbox":
            delivery = Outbox(store).begin()
            channel.send({"result": {"processing_id": delivery.processing.processing_id,
                                     "delivery_epoch": delivery.delivery_epoch} if delivery else None})
            return
        else:
            result = queue.complete(principal, CompleteRequest.from_dict(body))
        channel.send({"result": result.to_dict() if result else None})
    except IngressRejected as error:
        channel.send({"error": error.code})
    except BaseException as error:
        channel.send({"unexpected": type(error).__name__ + ": " + str(error)})
    finally:
        channel.close()


def crash(path, operation, body, identity, now, window):
    store = RuntimeStore(path, clock=lambda: now)
    queue = JobQueue(store)
    principal = Principal.from_dict(identity)
    event = store.event
    target = {"submit": "forecast-accepted", "claim": "job-claimed", "outbox": "processing-started"}[operation]

    def interrupted(c, time_ms, kind, payload):
        event(c, time_ms, kind, payload)
        if window == "before-commit" and kind == target:
            os._exit(17)

    store.event = interrupted
    if operation == "submit":
        queue.submit(principal, ForecastSubmission.from_dict(body))
    elif operation == "claim":
        queue.claim(principal, ClaimRequest.from_dict(body), lease_ms=30000)
    else:
        Outbox(store).begin()
    os._exit(18)  # operation committed but caller never got an application/HTTP response


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime.sqlite3"
        self.clock, self.store, self.queue, self.principal = setup_runtime(self.path)
        self.ctx = multiprocessing.get_context("spawn")

    def tearDown(self):
        self.temp.cleanup()

    def race(self, operations, now):
        gate = self.ctx.Event()
        children, channels = [], []
        try:
            for operation, body, principal in operations:
                reader, writer = self.ctx.Pipe(duplex=False)
                child = self.ctx.Process(target=contend, args=(str(self.path), operation, body,
                                         principal.to_dict(), now, gate, writer))
                child.start()
                writer.close()
                children.append(child)
                channels.append(reader)
            for channel in channels:
                self.assertTrue(channel.poll(15), "Child did not reach race barrier")
                self.assertEqual(channel.recv(), "ready")
            gate.set()
            results = []
            for channel in channels:
                self.assertTrue(channel.poll(15), "Child did not finish bounded SQLite operation")
                result = channel.recv()
                self.assertNotIn("unexpected", result, result)
                results.append(result)
            for child in children:
                child.join(15)
                self.assertEqual(child.exitcode, 0)
            self.clock.now = now
            return results
        finally:
            gate.set()
            for child in children:
                if child.is_alive():
                    child.terminate()
                child.join(5)
                child.close()
            for channel in channels:
                channel.close()

    def hard_exit(self, operation, body, now, window):
        child = self.ctx.Process(target=crash, args=(str(self.path), operation, body,
                                 self.principal.to_dict(), now, window))
        child.start()
        try:
            child.join(15)
            self.assertEqual(child.exitcode, 17 if window == "before-commit" else 18)
        finally:
            if child.is_alive():
                child.terminate()
                child.join(5)
            child.close()
        self.clock.now = now

    def counts(self):
        with self.store.connection() as c:
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            return tuple(c.execute("SELECT count(*) FROM " + name).fetchone()[0]
                         for name in ("submissions", "processing", "outbox"))

    def test_two_processes_claim_one_job_only_once(self):
        enqueue(self.queue)
        second = replace(self.principal, worker_id="worker-b")
        self.queue.configure_worker(second)
        body = ClaimRequest("claim-race").to_dict()
        results = self.race([("claim", body, self.principal), ("claim", body, second)], 100000)
        self.assertEqual(sum(r.get("result") is not None for r in results), 1)
        self.assertEqual(self.queue.inspect("job-one").attempt, 1)

    def test_two_processes_same_worker_same_claim_get_identical_lease(self):
        enqueue(self.queue)
        operation = ("claim", ClaimRequest("claim-race").to_dict(), self.principal)
        results = self.race([operation, operation], 100000)
        self.assertEqual(results[0], results[1])

    def test_concurrent_first_open_migrates_empty_database_once(self):
        self.path = Path(self.temp.name) / "fresh.sqlite3"
        operation = ("initialize", {}, self.principal)
        results = self.race([operation, operation], 100000)
        self.assertEqual(results, [{"result": "initialized"}, {"result": "initialized"}])
        with RuntimeStore(self.path).connection() as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_concurrent_scheduler_ticks_materialize_one_job(self):
        from runtime_support import job
        schedule = Schedule("scheduled", "dummy-agent", "test-0.1", "paper", 1, "control",
                            ("par:BTC",), 10000, 120000, job().limits)
        operation = ("schedule", schedule.to_dict(), self.principal)
        results = self.race([operation, operation], 100000)
        self.assertEqual(sum(r.get("result") is not None for r in results), 1)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_expired_heartbeat_cannot_resurrect_old_lease_during_reclaim(self):
        enqueue(self.queue)
        old = claim(self.queue, self.principal)
        results = self.race([("heartbeat", old.lease.ref.to_dict(), self.principal),
                             ("claim", ClaimRequest("new-claim").to_dict(), self.principal)], 130000)
        self.assertIn(results[0].get("error"), {"LEASE_LOST", "FORBIDDEN"})
        self.assertEqual(results[1]["result"]["job"]["attempt"], 2)

    def test_two_outbox_reservers_cannot_deliver_same_processing_twice(self):
        enqueue(self.queue)
        self.clock.now = 108001
        sent = submission(claim(self.queue, self.principal), created=108001)
        self.queue.submit(self.principal, sent)
        operation = ("outbox", {}, self.principal)
        results = self.race([operation, operation], 108001)
        self.assertEqual(sum(r.get("result") is not None for r in results), 1)

    def test_new_submission_ids_same_batch_race_produce_one_receipt_and_outbox(self):
        enqueue(self.queue)
        sent = submission(claim(self.queue, self.principal))
        results = self.race([("submit", sent.to_dict(), self.principal),
                             ("submit", replace(sent, submission_id="alias").to_dict(), self.principal)], 108001)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_conflicting_payload_race_cannot_overwrite_winner(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        results = self.race([("submit", submission(claimed).to_dict(), self.principal),
                             ("submit", submission(claimed, direction="down").to_dict(), self.principal)], 108001)
        self.assertEqual(sum("result" in r for r in results), 1)
        self.assertEqual(sum(r.get("error") == "ID_CONFLICT" for r in results), 1)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_submit_and_complete_race_have_one_analysis_terminal(self):
        enqueue(self.queue)
        claimed = claim(self.queue, self.principal)
        complete = CompleteRequest("complete", claimed.lease.ref, "no_forecast", "No result")
        results = self.race([("submit", submission(claimed).to_dict(), self.principal),
                             ("complete", complete.to_dict(), self.principal)], 108001)
        self.assertEqual(sum("result" in r for r in results), 1)
        self.assertEqual(sum(r.get("error") == "ID_CONFLICT" for r in results), 1)
        status = self.queue.inspect("job-one").status
        self.assertIn(status, {"submitted", "completed_without_forecast"})
        self.assertEqual(self.counts(), (1, 1, 1) if status == "submitted" else (0, 0, 0))

    def test_hard_exit_before_submission_commit_rolls_back_every_acceptance_record(self):
        enqueue(self.queue)
        sent = submission(claim(self.queue, self.principal))
        self.hard_exit("submit", sent.to_dict(), 108001, "before-commit")
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.queue.inspect("job-one").status, "running")
        self.queue.submit(self.principal, sent)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_hard_exit_after_submission_commit_replays_without_revalidating_expired_lease(self):
        enqueue(self.queue)
        sent = submission(claim(self.queue, self.principal))
        self.hard_exit("submit", sent.to_dict(), 108001, "after-commit")
        self.assertEqual(self.counts(), (1, 1, 1))
        self.clock.now = 230000
        receipt = self.queue.submit(self.principal, sent)
        self.assertEqual(receipt.received_ms, 108001)
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(self.queue.inspect("job-one").attempt, 1)

    def test_lost_claim_response_does_not_increment_attempt_after_commit(self):
        enqueue(self.queue)
        body = ClaimRequest("claim-lost").to_dict()
        self.hard_exit("claim", body, 100000, "after-commit")
        replay = self.queue.claim(self.principal, ClaimRequest.from_dict(body), lease_ms=30000)
        self.assertEqual(replay.job.attempt, 1)
        self.assertEqual(self.queue.inspect("job-one").lease_epoch, 1)

    def test_claim_crash_before_commit_leaves_job_queued(self):
        enqueue(self.queue)
        self.hard_exit("claim", ClaimRequest("claim-lost").to_dict(), 100000, "before-commit")
        self.assertEqual(self.queue.inspect("job-one").status, "queued")
        self.assertEqual(claim(self.queue, self.principal).job.attempt, 1)

    def test_outbox_crash_before_commit_preserves_pending_reservation(self):
        enqueue(self.queue)
        sent = submission(claim(self.queue, self.principal))
        self.clock.now = 108001
        self.queue.submit(self.principal, sent)
        self.hard_exit("outbox", {}, 108001, "before-commit")
        self.assertEqual(Outbox(self.store).begin().delivery_epoch, 1)

    def test_outbox_crash_after_commit_recovers_same_processing_and_fences_old_delivery(self):
        enqueue(self.queue)
        sent = submission(claim(self.queue, self.principal))
        self.clock.now = 108001
        receipt = self.queue.submit(self.principal, sent)
        self.hard_exit("outbox", {}, 108001, "after-commit")
        outbox = Outbox(self.store)
        self.assertEqual(outbox.recover(), 1)
        new = outbox.begin()
        self.assertEqual(new.processing.processing_id, receipt.processing_id)
        self.assertGreater(new.delivery_epoch, 1)
        with self.assertRaises(IngressRejected):
            outbox.finish(receipt.processing_id, 1, "complete")
        outbox.finish(receipt.processing_id, new.delivery_epoch, "complete")
        self.assertIsNone(outbox.begin())
        self.assertEqual(self.queue.inspect("job-one").status, "submitted")
