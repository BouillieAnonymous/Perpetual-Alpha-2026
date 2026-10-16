"""Real sockets, SQLite, process death and restart; no exchange/LLM/network dependency."""
import hashlib
import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from perpetual_alpha.contracts import Forecast, ForecastBatch
from perpetual_alpha.journal import Journal, canonical
from perpetual_alpha.runtime.contracts import AnalysisJob, JobLimits
from perpetual_alpha.runtime.ingress import IngressRejected, Principal
from perpetual_alpha.runtime.jobs import JobQueue
from perpetual_alpha.runtime.mock_config import load_config
from perpetual_alpha.runtime.mock_consumer import MockConsumer
from perpetual_alpha.runtime.mock_fixture import context_for, initial_account, request_for
from perpetual_alpha.runtime.process_lock import ProcessLock
from perpetual_alpha.runtime.store import APPLICATION_ID, SCHEMA_VERSION, RuntimeStore
from perpetual_alpha.transport.dummy_worker import DummyWorker
from perpetual_alpha.transport.fixture_gateway import query_fixture
from perpetual_alpha.transport.http_client import HTTPClient, RemoteError
from perpetual_alpha.transport.http_server import AgentHTTPServer, load_credentials
from perpetual_alpha.transport.messages import (
    ClaimRequest,
    ClaimResponse,
    CompleteRequest,
    CompleteResponse,
    DataQueryRequest,
    DataQueryResponse,
    FeedbackResponse,
    ForecastSubmission,
    HeartbeatRequest,
    HeartbeatResponse,
    SubmissionResponse,
)

ROOT = Path(__file__).resolve().parents[1]
CHILD_ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
PYTHON = sys._base_executable
TOKEN = "synthetic-test-token-not-an-exchange-key-12345678"


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = load_config(ROOT / "config/mock-http.toml")
        self.now = 100008
        self.store = RuntimeStore(self.directory / "runtime.db", clock=lambda: self.now)
        self.queue = JobQueue(self.store)
        s = self.config["service"]
        self.principal = Principal("worker-a", s["producer_id"], s["producer_version"], "control")
        self.queue.configure_worker(self.principal)
        self.queue.configure_control(s["control_scope"], s["producer_id"], s["producer_version"], 1)
        self.server = AgentHTTPServer(("127.0.0.1", 0), self.queue,
                                      [(hashlib.sha256(TOKEN.encode()).digest(), self.principal)], s)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.client = HTTPClient(self.url, TOKEN)
        self.journal = Journal(self.directory / "mock.db")
        self.addCleanup(self.journal.close)
        with self.journal.connection:
            self.journal.connection.execute("INSERT INTO simulation_state VALUES(1,?)",
                                           (canonical(initial_account(self.config["simulation"], self.now)),))
        self.consumer = MockConsumer(self.store, self.journal, self.config["simulation"])

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def enqueue(self, name="one", asof=100000):
        s = self.config["service"]
        job = AnalysisJob("job:" + name, "ctx:" + name, s["producer_id"], s["producer_version"],
                          s["control_scope"], s["control_epoch"], "control", tuple(self.config["simulation"]["quantities"]),
                          asof, self.now, asof + 120000, "queued", 0, 0, JobLimits(10, 100, 262144, 2, 90000, 20000))
        context, requests = context_for(self.config["simulation"], job.context_id, asof,
                                        initial_account(self.config["simulation"], asof))
        self.queue.enqueue(job, context, requests)
        return job

    def claim(self, request_id="claim-one"):
        return self.client.call("/v1/jobs/claim", ClaimResponse, ClaimRequest(request_id))

    def route(self, claimed, action):
        return "/v1/jobs/" + quote(claimed.job.job_id, safe="") + "/" + action

    def submission(self, claimed):
        j = claimed.job
        batch = ForecastBatch("batch:" + j.job_id, j.context_id, j.producer_id, j.producer_version,
                              self.now, tuple(Forecast(s, j.asof_ms, j.asof_ms + 90000, 3600000,
                                                       "ready", "up", ("fixture-quotes",)) for s in j.symbols))
        return ForecastSubmission("submit-one", claimed.lease.ref, batch)

    def test_http_roundtrip_duplicate_alias_and_feedback(self):
        self.enqueue()
        claimed = self.claim()
        self.assertEqual(self.claim(), claimed)
        heartbeat = self.client.call(self.route(claimed, "heartbeat"), HeartbeatResponse,
                                     HeartbeatRequest(claimed.lease.ref))
        self.assertEqual(heartbeat.lease.ref, claimed.lease.ref)
        query = DataQueryRequest("q", claimed.lease.ref, request_for(claimed.job.symbols, 90000))
        first = self.client.call(self.route(claimed, "data/query"), DataQueryResponse, query)
        self.assertEqual(first, self.client.call(self.route(claimed, "data/query"), DataQueryResponse, query))
        self.assertEqual(first.evidence_ref, "fixture-quotes")
        submission = self.submission(claimed)
        first = self.client.call(self.route(claimed, "forecast"), SubmissionResponse, submission)
        alias = replace(submission, submission_id="another-id")
        self.assertEqual(first, self.client.call(self.route(claimed, "forecast"), SubmissionResponse, alias))
        self.assertTrue(self.consumer.step())
        feedback = self.client.call(self.route(claimed, "feedback"), FeedbackResponse)
        self.assertEqual(feedback.processing.status, "completed")
        self.assertIsNotNone(feedback.processing.verdict_id)
        self.assertIsNotNone(feedback.processing.report_id)
        self.assertEqual(self.journal.connection.execute("SELECT count(*) FROM decisions").fetchone()[0], 1)
        self.assertEqual(self.journal.reports(self.now)[-1]["status"], "completed")
        self.now += 200000
        self.assertEqual(first, self.client.call(self.route(claimed, "forecast"), SubmissionResponse, submission))
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM submissions").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM fixture_queries").fetchone()[0], 1)

    def test_http_timeout_after_commit_replays_same_receipt(self):
        self.enqueue()
        claimed = self.claim()
        message = self.submission(claimed)
        real_submit = self.queue.submit
        committed = threading.Event()

        def lost_reply(*args, **kwargs):
            result = real_submit(*args, **kwargs)
            committed.set()
            time.sleep(.2)
            return result

        with patch.object(self.queue, "submit", lost_reply):
            impatient = HTTPClient(self.url, TOKEN, timeout=.05)
            with self.assertRaises((OSError, TimeoutError)):
                impatient.call(self.route(claimed, "forecast"), SubmissionResponse, message)
            self.assertTrue(committed.wait(1))
        response = self.client.call(self.route(claimed, "forecast"), SubmissionResponse, message)
        self.assertEqual(response.receipt, self.queue.feedback(self.principal, claimed.job.job_id).receipt)
        self.consumer.step()
        self.assertEqual(self.journal.connection.execute("SELECT count(*) FROM decisions").fetchone()[0], 1)

    def test_auth_ownership_conflict_and_route_binding(self):
        self.enqueue()
        claimed = self.claim()
        with self.assertRaises(RemoteError) as error:
            HTTPClient(self.url, "wrong-token").call(self.route(claimed, "feedback"), FeedbackResponse)
        self.assertEqual(error.exception.code, "UNAUTHENTICATED")
        other = Principal("worker-b", self.principal.producer_id, self.principal.producer_version, "control")
        self.queue.configure_worker(other)
        self.server.credentials.append((hashlib.sha256(b"other-token").digest(), other))
        with self.assertRaises(RemoteError) as error:
            HTTPClient(self.url, "other-token").call(self.route(claimed, "feedback"), FeedbackResponse)
        self.assertEqual(error.exception.code, "FORBIDDEN")
        with self.assertRaises(RemoteError):
            self.client.call("/v1/jobs/another/forecast", SubmissionResponse, self.submission(claimed))
        request = self.submission(claimed)
        self.client.call(self.route(claimed, "forecast"), SubmissionResponse, request)
        with self.assertRaises(RemoteError) as error:
            self.client.call(self.route(claimed, "forecast"), SubmissionResponse,
                             replace(request, forecast_batch=replace(request.forecast_batch, forecast_batch_id="changed")))
        self.assertEqual(error.exception.code, "ID_CONFLICT")

    def test_query_scope_budget_and_cutoff(self):
        self.enqueue()
        claimed = self.claim()
        request = DataQueryRequest("q", claimed.lease.ref, request_for(claimed.job.symbols, 90000))
        with self.assertRaises(RemoteError) as error:
            self.client.call(self.route(claimed, "data/query"), DataQueryResponse,
                             replace(request, data_request=replace(request.data_request, source="external")))
        self.assertEqual(error.exception.code, "FORBIDDEN")
        for n in range(10):
            result = query_fixture(self.queue, self.principal, replace(request, request_id=str(n)))
            self.assertTrue(all(r.received_ms <= claimed.job.asof_ms for r in result.records))
        with self.assertRaises(IngressRejected) as error:
            query_fixture(self.queue, self.principal, replace(request, request_id="over-budget"))
        self.assertEqual(error.exception.code, "LIMIT_EXCEEDED")
        self.assertEqual(result, query_fixture(self.queue, self.principal, replace(request, request_id="9")))

    def test_control_switch_before_consume_blocks_without_mock_effect(self):
        self.enqueue()
        claimed = self.claim()
        self.client.call(self.route(claimed, "forecast"), SubmissionResponse, self.submission(claimed))
        self.queue.configure_control(self.config["service"]["control_scope"], self.principal.producer_id,
                                     self.principal.producer_version, 2)
        self.consumer.step()
        feedback = self.client.call(self.route(claimed, "feedback"), FeedbackResponse)
        self.assertEqual(feedback.processing.reason_code, "STALE_RESULT")
        self.assertEqual(self.journal.reports(self.now), [])

    def test_consumption_watermark_prevents_old_result(self):
        self.enqueue()
        claimed = self.claim()
        self.client.call(self.route(claimed, "forecast"), SubmissionResponse, self.submission(claimed))
        with self.store.transaction() as (c, _):
            c.execute("UPDATE controls SET consumed_asof_ms=?", (claimed.job.asof_ms + 1,))
        self.consumer.step()
        self.assertEqual(self.queue.feedback(self.principal, claimed.job.job_id).processing.reason_code, "STALE_RESULT")
        self.assertEqual(self.journal.reports(self.now), [])

    def test_control_change_after_plan_is_rechecked(self):
        self.enqueue()
        claimed = self.claim()
        self.queue.submit(self.principal, self.submission(claimed))
        original = self.consumer._plan

        def switch(delivery):
            plan = original(delivery)
            self.queue.configure_control(self.config["service"]["control_scope"], self.principal.producer_id,
                                         self.principal.producer_version, 2)
            return plan

        with patch.object(self.consumer, "_plan", switch):
            self.consumer.step()
        self.assertEqual(self.journal.reports(self.now), [])
        self.assertEqual(self.queue.feedback(self.principal, claimed.job.job_id).processing.reason_code, "STALE_RESULT")

    def test_complete_and_empty_claim(self):
        self.assertIsNone(self.claim("empty"))
        self.enqueue()
        self.assertIsNone(self.claim("empty"))
        claimed = self.claim()
        message = CompleteRequest("complete", claimed.lease.ref, "no_forecast", "SYNTHETIC abstention")
        response = self.client.call(self.route(claimed, "complete"), CompleteResponse, message)
        self.assertEqual(response.status, "completed_without_forecast")
        self.assertEqual(response, self.client.call(self.route(claimed, "complete"), CompleteResponse, message))

    def test_invalid_json_body_limit_depth_and_chunked(self):
        for body, extra, status in [(b'{"request_id":"x","request_id":"y"}', {}, 400),
                                     (b'[' * 40 + b']' * 40, {}, 400),
                                     (b'{}', {"Transfer-Encoding": "chunked"}, 400),
                                     (b'{}', {"Content-Length": "99999999"}, 429)]:
            with self.subTest(body=body[:30], extra=extra):
                connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
                try:
                    headers = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json", **extra}
                    connection.request("POST", "/v1/jobs/claim", body, headers)
                    self.assertEqual(connection.getresponse().status, status)
                finally:
                    connection.close()

    def test_unknown_outcome_does_not_auto_reexecute(self):
        self.enqueue()
        claimed = self.claim()
        self.queue.submit(self.principal, self.submission(claimed))
        from perpetual_alpha.simulation import MockExecution
        original = MockExecution.apply
        with patch.object(MockExecution, "apply", lambda obj, d: original(obj, d, simulate_unknown=True)):
            self.consumer.step()
        self.assertEqual(self.queue.feedback(self.principal, claimed.job.job_id).processing.reason_code, "NETWORK_UNKNOWN")
        self.consumer.outbox.recover()
        self.consumer.step()
        self.assertEqual(self.journal.connection.execute("SELECT count(*) FROM decisions").fetchone()[0], 1)

    def test_mock_configuration_identity_immutable(self):
        config = {**self.config["simulation"], "gross_limit_usd": "999"}
        with self.assertRaises(ValueError):
            MockConsumer(self.store, self.journal, config)

    def test_revoked_worker_cannot_replay_an_accepted_result(self):
        self.enqueue()
        claimed = self.claim()
        self.queue.submit(self.principal, self.submission(claimed))
        self.queue.configure_worker(self.principal, enabled=False)
        with self.assertRaises(RemoteError) as error:
            self.client.call(self.route(claimed, "forecast"), SubmissionResponse, self.submission(claimed))
        self.assertEqual(error.exception.code, "FORBIDDEN")

    def test_late_submit_and_expired_queued_processing_never_execute(self):
        self.enqueue()
        claimed = self.claim()
        submission = self.submission(claimed)
        self.now += 30000
        with self.assertRaises(RemoteError) as error:
            self.client.call(self.route(claimed, "forecast"), SubmissionResponse, submission)
        self.assertEqual(error.exception.code, "LEASE_LOST")
        new_claim = self.claim("second-attempt")
        self.queue.submit(self.principal, self.submission(new_claim))
        self.now += 90000
        self.consumer.step()
        self.assertEqual(self.queue.feedback(self.principal, claimed.job.job_id).processing.status, "blocked")
        self.assertEqual(self.journal.reports(self.now), [])

    def test_concurrent_http_submissions_share_one_result(self):
        self.enqueue()
        claimed = self.claim()
        submission = self.submission(claimed)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(HTTPClient(self.url, TOKEN).call, self.route(claimed, "forecast"),
                                   SubmissionResponse, replace(submission, submission_id="alias-" + str(n)))
                       for n in range(4)]
            responses = [f.result(timeout=5) for f in futures]
        self.assertTrue(all(r == responses[0] for r in responses))
        self.consumer.step()
        self.assertEqual(self.journal.connection.execute("SELECT count(*) FROM decisions").fetchone()[0], 1)

    def test_concurrent_queries_cannot_exceed_budget(self):
        self.enqueue()
        claimed = self.claim()
        request = DataQueryRequest("q", claimed.lease.ref, request_for(claimed.job.symbols, 90000))
        from concurrent.futures import ThreadPoolExecutor

        def query(n):
            try:
                return query_fixture(self.queue, self.principal, replace(request, request_id=str(n)))
            except IngressRejected as error:
                return error.code

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(query, range(16)))
        self.assertEqual(results.count("LIMIT_EXCEEDED"), 6)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM fixture_queries").fetchone()[0], 10)

    def test_recovery_before_effect_reuses_frozen_decision_and_configuration(self):
        self.enqueue()
        claimed = self.claim()
        self.queue.submit(self.principal, self.submission(claimed))
        delivery = self.consumer.outbox.begin()
        decision, _, old = self.consumer._plan(delivery)
        # The new process has a different demo policy, but its pending plan remains immutable.
        updated = {**self.config["simulation"], "configuration_id": "new-demo-policy",
                   "quantities": {"par:BTC": ".0003", "par:ETH": ".002"}}
        restarted = MockConsumer(self.store, self.journal, updated)
        restarted.reconcile()
        restarted.outbox.recover()
        restarted.step()
        payload = json.loads(self.journal.connection.execute("SELECT payload FROM decisions").fetchone()[0])
        self.assertEqual(payload, decision.to_dict())
        self.assertEqual(old["configuration_id"], "mock-pipeline-0.1")

    def test_expired_frozen_plan_is_blocked_after_restart(self):
        self.enqueue()
        claimed = self.claim()
        self.queue.submit(self.principal, self.submission(claimed))
        delivery = self.consumer.outbox.begin()
        self.consumer._plan(delivery)
        self.now += 30000
        self.consumer.outbox.recover()
        self.consumer.step()
        self.assertEqual(self.queue.feedback(self.principal, claimed.job.job_id).processing.reason_code, "EXPIRED")
        self.assertEqual(self.journal.reports(self.now), [])

    def test_rate_and_paused_core_are_bounded(self):
        self.server.ready = False
        with self.assertRaises(RemoteError) as error:
            self.claim()
        self.assertEqual(error.exception.code, "UNAVAILABLE")
        self.server.ready = True
        from collections import deque
        self.server.rates[self.principal.worker_id] = deque([time.monotonic()] * 40)
        with self.assertRaises(RemoteError) as error:
            self.claim()
        self.assertEqual(error.exception.code, "LIMIT_EXCEEDED")

    def test_client_rejects_redirect_without_forwarding_token(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:1/steal")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(ValueError, "Redirect refused"):
                HTTPClient("http://127.0.0.1:" + str(server.server_port), TOKEN).call(
                    "/v1/jobs/claim", ClaimResponse, ClaimRequest("redirect"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_hard_exit_after_mock_commit_before_feedback_reconciles_even_after_epoch_switch(self):
        self.enqueue()
        claimed = self.claim()
        self.queue.submit(self.principal, self.submission(claimed))
        code = '''
import os,sys
from perpetual_alpha.runtime.store import RuntimeStore
from perpetual_alpha.journal import Journal
from perpetual_alpha.runtime.mock_config import load_config
from perpetual_alpha.runtime.mock_consumer import MockConsumer
from perpetual_alpha.simulation import MockExecution
original=MockExecution.apply
def crash(self,decision):
    original(self,decision)
    os._exit(72)
MockExecution.apply=crash
MockConsumer(RuntimeStore(sys.argv[1],clock=lambda:100008),Journal(sys.argv[2]),load_config(sys.argv[3])["simulation"]).step()
'''
        result = subprocess.run([PYTHON, "-c", code, str(self.store.path), str(self.directory / "mock.db"),
                                 str(ROOT / "config/mock-http.toml")], capture_output=True, env=CHILD_ENV, timeout=20)
        self.assertEqual(result.returncode, 72, result.stderr.decode())
        self.now += 200000
        self.queue.configure_control(self.config["service"]["control_scope"], self.principal.producer_id,
                                     self.principal.producer_version, 2)
        self.consumer.reconcile()
        self.consumer.outbox.recover()
        feedback = self.queue.feedback(self.principal, claimed.job.job_id)
        self.assertEqual(feedback.processing.status, "completed")
        self.assertEqual(self.journal.connection.execute("SELECT count(*) FROM decisions").fetchone()[0], 1)
        self.assertFalse(self.consumer.step())


class ProcessTests(unittest.TestCase):
    def test_v1_migration_preserves_existing_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "v1.db"
            connection = sqlite3.connect(path)
            connection.executescript((ROOT / "src/perpetual_alpha/runtime/migrations/001_runtime.sql").read_text())
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1")
            connection.execute("INSERT INTO audit VALUES(1,1,'kept','{}')")
            connection.commit()
            connection.close()
            store = RuntimeStore(path)
            self.assertEqual(store.audit()[0]["kind"], "kept")
            with store.connection() as c:
                self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_single_process_lock_across_processes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "process.lock"
            code = "import sys; from perpetual_alpha.runtime.process_lock import ProcessLock; ProcessLock(sys.argv[1]).__enter__()"
            with ProcessLock(path):
                result = subprocess.run([PYTHON, "-c", code, str(path)], capture_output=True, env=CHILD_ENV, timeout=10)
                self.assertNotEqual(result.returncode, 0)
            result = subprocess.run([PYTHON, "-c", code, str(path)], capture_output=True, env=CHILD_ENV, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_credentials_and_client_origin_validation(self):
        for url in ("ftp://localhost", "http://user:secret@localhost", "http://localhost/?redirect=evil", "http://localhost/path"):
            with self.assertRaises(ValueError):
                HTTPClient(url, TOKEN)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "credentials.json"
            entry = {"token": TOKEN, "worker_id": "a", "producer_id": "dummy-agent", "producer_version": "offline-0.1", "role": "control"}
            path.write_text(json.dumps([entry]), encoding="utf-8")
            self.assertEqual(load_credentials(path)[0][1].worker_id, "a")
            path.write_text(json.dumps([entry, entry]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_credentials(path)

    def test_real_core_restart_and_worker_hard_exit_resume_same_result(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            token_file, credential_file = directory / "token", directory / "credentials.json"
            token_file.write_text(TOKEN, encoding="utf-8")
            credential_file.write_text(json.dumps([{"token": TOKEN, "worker_id": "process-worker",
                                                   "producer_id": "dummy-agent", "producer_version": "offline-0.1", "role": "control"}]))
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            command = [PYTHON, "-u", "-m", "perpetual_alpha.runtime.http_core", "--port", str(port),
                       "--credentials", str(credential_file), "--database", str(directory / "runtime.db"),
                       "--journal", str(directory / "mock.db")]
            log = (directory / "core.log").open("w+")
            client = HTTPClient("http://127.0.0.1:" + str(port), TOKEN, timeout=.5)

            def start():
                core = subprocess.Popen(command, stdout=log, stderr=log, cwd=ROOT, env=CHILD_ENV)
                try:
                    until = time.monotonic() + 15
                    while time.monotonic() < until:
                        if core.poll() is not None:
                            log.seek(0)
                            self.fail(log.read())
                        try:
                            client.call("/v1/jobs/claim", ClaimResponse, ClaimRequest("startup-empty"))
                            return core
                        except OSError:
                            time.sleep(.05)
                    self.fail("Core startup timed out")
                except BaseException:
                    core.kill()
                    core.wait(5)
                    raise

            try:
                core = start()
            except BaseException:
                log.close()
                raise
            try:
                # startup probe might already own the scheduled job; replay the exact claim ID.
                claimed = None
                for n in range(50):
                    claimed = client.call("/v1/jobs/claim", ClaimResponse, ClaimRequest("startup-empty" if n == 0 else str(n)))
                    if claimed:
                        break
                    time.sleep(.02)
                self.assertIsNotNone(claimed)
                state = directory / "worker.json"
                worker = DummyWorker(client, state, load_config(ROOT / "config/mock-http.toml"))
                worker.state = {"claim_id": "original-poll", "claim": claimed.to_dict()}
                worker.save()
                core.kill()
                core.wait(5)
                core = start()
                # An actual Worker process dies after the Core accepted its persisted submission.
                code = '''
import os,sys
from perpetual_alpha.transport.http_client import HTTPClient
from perpetual_alpha.transport.dummy_worker import DummyWorker
from perpetual_alpha.runtime.mock_config import load_config
client=HTTPClient(sys.argv[1],sys.argv[2])
original=client.call
def crash(path,cls,message=None):
    result=original(path,cls,message)
    if path.endswith('/forecast'): os._exit(73)
    return result
client.call=crash
DummyWorker(client,sys.argv[3],load_config(sys.argv[4])).cycle()
'''
                result = subprocess.run([PYTHON, "-c", code, client.base_url, TOKEN, str(state),
                                         str(ROOT / "config/mock-http.toml")], capture_output=True, env=CHILD_ENV, timeout=15)
                self.assertEqual(result.returncode, 73, result.stderr.decode())
                saved = json.loads(state.read_text())
                self.assertIn("submission", saved)
                result = subprocess.run([PYTHON, "-m", "perpetual_alpha.transport.dummy_worker",
                                         "--core-url", client.base_url, "--token-file", str(token_file),
                                         "--state", str(state), "--once"], cwd=ROOT, capture_output=True, env=CHILD_ENV, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                output = json.loads(result.stdout)
                self.assertTrue(output["simulated"])
                self.assertEqual(output["feedback"]["processing"]["status"], "completed")
                self.assertEqual(output["feedback"]["receipt"]["forecast_batch_id"],
                                 saved["submission"]["forecast_batch"]["forecast_batch_id"])
                with sqlite3.connect(directory / "mock.db") as c:
                    self.assertEqual(c.execute("SELECT count(*) FROM decisions").fetchone()[0], 1)
                c.close()
            finally:
                core.kill()
                core.wait(5)
                log.close()
