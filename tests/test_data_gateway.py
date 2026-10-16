"""Actual SQLite + HTTP integration for pinned multi-round historical evidence."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from perpetual_alpha.contracts import Forecast, ForecastBatch
from perpetual_alpha.historical.store import HistoricalStore
from perpetual_alpha.interfaces import DataRequest
from perpetual_alpha.journal import canonical
from perpetual_alpha.runtime.contracts import AnalysisJob, JobLimits
from perpetual_alpha.runtime.ingress import IngressRejected, Principal
from perpetual_alpha.runtime.jobs import JobQueue
from perpetual_alpha.runtime.mock_config import load_config
from perpetual_alpha.runtime.outbox import Outbox
from perpetual_alpha.runtime.store import RuntimeStore
from perpetual_alpha.transport.data_gateway import DataGateway, DataPolicy, response_digest
from perpetual_alpha.transport.history_worker import HistoricalDummyWorker
from perpetual_alpha.transport.http_client import HTTPClient
from perpetual_alpha.transport.http_server import AgentHTTPServer
from perpetual_alpha.transport.messages import (
    ClaimRequest,
    DataQueryRequest,
    ForecastSubmission,
)

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "gateway-test-token-not-an-exchange-key-12345678"


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.now, self.history_now = 1200008, 1200000
        self.store = RuntimeStore(self.folder / "runtime.db", clock=lambda: self.now)
        self.queue = JobQueue(self.store)
        self.history = HistoricalStore(self.folder / "history.db", clock=lambda: self.history_now)
        for symbol in ("par:BTC", "par:ETH"):
            bars = [{"s": symbol, "i": "1m", "t": opened, "T": opened + 59999, "o": "100", "h": "110",
                     "l": "90", "c": "105", "v": "1", "n": 2} for opened in range(0, 1200000, 60000)]
            self.history.record_candles(symbol, symbol, "1m", 0, 1200000, bars, self.history_now)
        self.principal = Principal("worker-a", "dummy-agent", "offline-0.1", "control")
        self.queue.configure_worker(self.principal)
        self.queue.configure_control("paper", self.principal.producer_id, self.principal.producer_version, 1)
        self.gateway = DataGateway(self.queue, HistoricalStore(self.history.path, read_only=True))
        self.policy = DataPolicy("test-policy/0.1", self.principal.producer_id, self.principal.producer_version,
                                 self.principal.role, ("par:BTC", "par:ETH", "par:HYPE"), ("1m",),
                                 ("open", "high", "low", "close", "volume", "trade_count"), 21600000)
        self.gateway.configure(self.policy)

    def claim(self, *, limits=None):
        item = AnalysisJob("dynamic-one", "context-one", self.principal.producer_id, self.principal.producer_version,
                           "paper", 1, self.principal.role, ("par:BTC",), 1200000, 1200008, 1320000,
                           "queued", 0, 0, limits or JobLimits(10, 100, 262144, 2, 90000, 90000))
        context = {"protocol_version": "1.0", "context_id": item.context_id, "asof_ms": item.asof_ms,
                   "account": None, "execution_reports": [], "datasets": {}}
        self.queue.enqueue(item, context, (), data_binding=self.gateway.binding(self.policy, item.asof_ms))
        return self.queue.claim(self.principal, ClaimRequest("one"), lease_ms=30000)

    def query(self, claimed, name="one", *, symbols=("par:BTC",), **changes):
        request = DataRequest("history-" + name, "hyperliquid-testnet/par", "candle", symbols,
                              ("close",), 300000, 100, 600000, False, {"interval": "1m", "end_ms": 1200000})
        return DataQueryRequest(name, claimed.lease.ref, replace(request, **changes))

    def submit(self, claimed, refs=("history-one",)):
        batch = ForecastBatch("batch-one", claimed.job.context_id, claimed.job.producer_id, claimed.job.producer_version,
                              self.now, (Forecast("par:BTC", claimed.job.asof_ms, claimed.job.asof_ms + 90000,
                                                  3600000, "ready", "up", refs),))
        return self.queue.submit(self.principal, ForecastSubmission("submission-one", claimed.lease.ref, batch))

    def test_two_windows_and_auxiliary_asset_forecast_seal_consumption(self):
        claimed = self.claim()
        one = self.gateway.query(self.principal, self.query(claimed))
        two = self.gateway.query(self.principal, self.query(claimed, "two", symbols=("par:ETH",), lookback_ms=600000))
        self.assertEqual((len(one.records), len(two.records)), (5, 10))
        self.assertEqual(one.asof_ms, two.asof_ms)
        self.assertEqual(response_digest(one), one.payload_digest)
        self.submit(claimed, refs=(one.evidence_ref, two.evidence_ref))
        delivery = Outbox(self.store).begin()
        self.assertEqual(set(delivery.manifest["context"]["datasets"]), {"history-one", "history-two"})
        self.assertEqual(len(delivery.manifest["requests"]), 2)
        dataset = delivery.manifest["context"]["datasets"]["history-one"]
        self.assertEqual(dataset["payload_digest"], one.payload_digest)
        self.assertIn("snapshot_seq", dataset["provenance"]["par:BTC"])

    def test_dataset_and_request_ids_cannot_change_definition(self):
        claimed = self.claim()
        message = self.query(claimed)
        self.gateway.query(self.principal, message)
        for changed in (replace(message, data_request=replace(message.data_request, lookback_ms=600000)),
                        replace(message, request_id="alias", data_request=replace(message.data_request, fields=("open",))),
                        replace(message, data_request=replace(message.data_request, dataset_id="new-dataset"))):
            with self.assertRaises(IngressRejected) as error:
                self.gateway.query(self.principal, changed)
            self.assertEqual(error.exception.code, "ID_CONFLICT")

    def test_exact_retry_alias_and_core_restart_replay_without_history(self):
        claimed = self.claim()
        message = self.query(claimed)
        first = self.gateway.query(self.principal, message)
        self.assertEqual(first, self.gateway.query(self.principal, message))
        restarted = DataGateway(JobQueue(RuntimeStore(self.store.path, clock=lambda: self.now)), self.gateway.history)
        with patch.object(restarted.history, "read_candles", side_effect=AssertionError("must use persisted response")):
            self.assertEqual(first, restarted.query(self.principal, message))
            alias = restarted.query(self.principal, replace(message, request_id="alias"))
        self.assertEqual(alias.payload_digest, first.payload_digest)
        self.assertEqual(alias.request_id, "alias")
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM gateway_datasets").fetchone()[0], 1)

    def test_revisions_and_new_backfills_do_not_change_job_snapshot(self):
        claimed = self.claim()
        self.history_now += 10000
        changed = {"s": "par:BTC", "i": "1m", "t": 1140000, "T": 1199999, "o": "100", "h": "110",
                   "l": "90", "c": "107", "v": "1", "n": 2}
        self.history.record_candles("later", "par:BTC", "1m", 1140000, 1200000, [changed], self.history_now)
        self.assertEqual(self.gateway.query(self.principal, self.query(claimed)).records[-1].data["close"], "105")
        self.assertEqual(self.gateway.query(self.principal, self.query(claimed, "second")).records[-1].data["close"], "105")

    def test_same_millisecond_write_after_job_creation_is_excluded(self):
        claimed = self.claim()
        bar = {"s": "par:HYPE", "i": "1m", "t": 1140000, "T": 1199999, "o": "100", "h": "110",
               "l": "90", "c": "107", "v": "1", "n": 2}
        self.history.record_candles("same-ms-later-commit", "par:HYPE", "1m", 1140000, 1200000, [bar], self.history_now)
        response = self.gateway.query(self.principal, self.query(claimed, symbols=("par:HYPE",)))
        self.assertEqual(response.status, "missing")
        self.assertIsNone(response.evidence_ref)

    def test_missing_snapshot_anchor_rejects_replaced_database(self):
        claimed = self.claim()
        with self.history.connection() as c:
            c.execute("UPDATE observations SET observation_id='replaced' WHERE seq=2")
        with self.assertRaises(IngressRejected) as error:
            self.gateway.query(self.principal, self.query(claimed))
        self.assertEqual(error.exception.code, "UNAVAILABLE")

    def test_incomplete_and_stale_quality_never_becomes_evidence(self):
        with self.history.connection() as c:
            c.execute("DELETE FROM candle_versions WHERE symbol='par:BTC' AND open_ms=1080000")
        claimed = self.claim()
        result = self.gateway.query(self.principal, self.query(claimed, parameters={"interval": "1m", "end_ms": 1140000}, max_age_ms=1))
        self.assertEqual(result.status, "stale")
        quality = json.loads(result.error)["quality"]["par:BTC"]
        self.assertTrue(quality["gaps"])
        self.assertIsNone(result.evidence_ref)
        with self.assertRaises(ValueError):
            self.submit(claimed)

    def test_truncation_is_not_ready_or_citable(self):
        claimed = self.claim()
        result = self.gateway.query(self.principal, self.query(claimed, symbols=("par:BTC", "par:ETH"), max_records=6))
        self.assertTrue(result.truncated)
        self.assertNotEqual(result.status, "ready")
        self.assertEqual(len(result.records), 6)
        self.assertIsNone(result.evidence_ref)

    def test_prediction_scope_does_not_expand_with_auxiliary_read_scope(self):
        claimed = self.claim()
        self.gateway.query(self.principal, self.query(claimed, symbols=("par:ETH",)))
        unsupported = ForecastBatch("batch-wrong", claimed.job.context_id, claimed.job.producer_id, claimed.job.producer_version,
                                    self.now, (Forecast("par:ETH", claimed.job.asof_ms, claimed.job.asof_ms + 90000,
                                                        3600000, "ready", "up", ("history-one",)),))
        with self.assertRaises(ValueError):
            self.queue.submit(self.principal, ForecastSubmission("wrong", claimed.lease.ref, unsupported))
        self.submit(claimed)
        # Separate job below is not needed: acceptance used BTC only despite ETH evidence.
        self.assertEqual(claimed.job.symbols, ("par:BTC",))

    def test_capability_blocks_sources_fields_symbols_windows_and_parameters(self):
        claimed = self.claim()
        changes = ({"source": "https://evil.invalid"}, {"kind": "sql"}, {"symbols": ("par:GOLD",)},
                   {"fields": ("private_key",)}, {"parameters": {"interval": "1m", "end_ms": 1200000, "path": "x"}},
                   {"parameters": {"interval": "1M", "end_ms": 1200000}}, {"max_age_ms": 21600001},
                   {"parameters": {"interval": "1m", "end_ms": 1260000}}, {"lookback_ms": 300001},
                   {"max_records": 101})
        with patch.object(self.gateway.history, "read_candles", side_effect=AssertionError("must reject before data read")):
            for values in changes:
                with self.subTest(values=values), self.assertRaises(IngressRejected):
                    self.gateway.query(self.principal, self.query(claimed, **values))

    def test_wrong_worker_and_lease_and_revocation(self):
        claimed = self.claim()
        message = self.query(claimed)
        other = replace(self.principal, worker_id="other")
        self.queue.configure_worker(other)
        with self.assertRaises(IngressRejected):
            self.gateway.query(other, message)
        with self.assertRaises(IngressRejected):
            self.gateway.query(self.principal, replace(message, lease=replace(message.lease, lease_token="fake")))
        self.gateway.query(self.principal, message)
        self.gateway.configure(replace(self.policy, policy_id="revoked", read_symbols=("par:ETH",)))
        with self.assertRaises(IngressRejected):
            self.gateway.query(self.principal, message)
        with self.assertRaises(IngressRejected):
            self.submit(claimed)

    def test_policy_replacement_requires_new_id(self):
        with self.assertRaises(ValueError):
            self.gateway.configure(replace(self.policy, read_symbols=("par:ETH",)))

    def test_new_attempt_cannot_cite_old_attempt_without_reading(self):
        claimed = self.claim()
        original = self.gateway.query(self.principal, self.query(claimed))
        self.now += 30000
        second = self.queue.claim(self.principal, ClaimRequest("second"), lease_ms=30000)
        self.assertEqual(second.job.attempt, 2)
        with self.assertRaises(ValueError):
            self.submit(second)
        replay = self.gateway.query(self.principal, self.query(second))
        self.assertEqual(replay, original)
        self.submit(second)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM gateway_datasets").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM gateway_reads").fetchone()[0], 2)

    def test_query_after_submit_cannot_change_manifest_or_budget(self):
        claimed = self.claim()
        self.gateway.query(self.principal, self.query(claimed))
        self.submit(claimed)
        with self.assertRaises(IngressRejected):
            self.gateway.query(self.principal, self.query(claimed, "late"))
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM gateway_datasets").fetchone()[0], 1)

    def test_query_which_finishes_after_submit_is_discarded(self):
        claimed = self.claim()
        self.gateway.query(self.principal, self.query(claimed))
        original = self.gateway._read

        def submit_first(*args):
            value = original(*args)
            self.submit(claimed)
            return value

        with patch.object(self.gateway, "_read", submit_first), self.assertRaises(IngressRejected):
            self.gateway.query(self.principal, self.query(claimed, "racing"))
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM gateway_datasets").fetchone()[0], 1)

    def test_control_switch_during_read_is_rechecked(self):
        claimed = self.claim()
        original = self.gateway._read

        def change(*args):
            result = original(*args)
            self.queue.configure_control("paper", self.principal.producer_id, self.principal.producer_version, 2)
            return result

        with patch.object(self.gateway, "_read", change), self.assertRaises(IngressRejected) as error:
            self.gateway.query(self.principal, self.query(claimed))
        self.assertEqual(error.exception.code, "STALE_RESULT")

    def test_concurrent_queries_count_and_record_budget_atomic(self):
        claimed = self.claim(limits=JobLimits(2, 100, 262144, 2, 90000, 90000))

        def query(n):
            try:
                return self.gateway.query(self.principal, self.query(claimed, str(n)))
            except IngressRejected as error:
                return error.code

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(query, range(6)))
        self.assertEqual(results.count("LIMIT_EXCEEDED"), 4)
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM gateway_datasets").fetchone()[0], 2)

    def test_evidence_provenance_counts_against_byte_budget(self):
        claimed = self.claim(limits=JobLimits(10, 100, 1000, 2, 90000, 90000))
        with self.assertRaises(IngressRejected) as error:
            self.gateway.query(self.principal, self.query(claimed))
        self.assertEqual(error.exception.code, "LIMIT_EXCEEDED")

    def test_query_aliases_cannot_grow_without_bound(self):
        claimed = self.claim(limits=JobLimits(1, 100, 262144, 2, 90000, 90000))
        message = self.query(claimed)
        for n in range(4):
            self.gateway.query(self.principal, replace(message, request_id=str(n)))
        with self.assertRaises(IngressRejected):
            self.gateway.query(self.principal, replace(message, request_id="over"))
        self.gateway.query(self.principal, replace(message, request_id="0"))

    def test_saved_response_or_provenance_corruption_is_rejected(self):
        claimed = self.claim()
        self.gateway.query(self.principal, self.query(claimed))
        with self.store.connection() as c:
            c.execute("UPDATE gateway_datasets SET provenance='{}'")
        with self.assertRaises(IngressRejected):
            self.submit(claimed)
        with self.assertRaises(IngressRejected):
            self.gateway.query(self.principal, self.query(claimed))

    def test_expired_query_does_not_read_or_spend_budget(self):
        claimed = self.claim()
        self.now += 30000
        with patch.object(self.gateway.history, "read_candles", side_effect=AssertionError("must not read")):
            with self.assertRaises(IngressRejected) as error:
                self.gateway.query(self.principal, self.query(claimed))
        self.assertEqual(error.exception.code, "LEASE_LOST")
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM gateway_datasets").fetchone()[0], 0)

    def test_accepted_manifest_is_immutable_and_detects_corruption(self):
        claimed = self.claim()
        self.gateway.query(self.principal, self.query(claimed))
        self.submit(claimed)
        with self.store.connection() as c:
            c.execute("UPDATE accepted_manifests SET payload='{}'")
        with self.assertRaises(IngressRejected):
            Outbox(self.store).begin()

    def test_local_history_read_does_not_hold_runtime_write_lock(self):
        claimed = self.claim()
        original = self.gateway.history.read_candles

        def another_writer(*args, **kwargs):
            with self.store.transaction():
                pass
            return original(*args, **kwargs)

        with patch.object(self.gateway.history, "read_candles", another_writer):
            self.assertEqual(self.gateway.query(self.principal, self.query(claimed)).status, "ready")

    def test_shadow_dummy_does_two_real_http_rounds_then_submits(self):
        self.principal = replace(self.principal, role="shadow")
        self.queue.configure_worker(self.principal)
        self.policy = replace(self.policy, role="shadow")
        self.gateway.configure(self.policy)
        claimed = self.claim()
        settings = load_config(ROOT / "config/mock-http.toml")
        server = AgentHTTPServer(("127.0.0.1", 0), self.queue,
                                  [(hashlib.sha256(TOKEN.encode()).digest(), self.principal)], settings["service"],
                                  data_gateway=self.gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            worker = HistoricalDummyWorker(HTTPClient("http://127.0.0.1:" + str(server.server_port), TOKEN),
                                           self.folder / "worker.json", settings)
            worker.state = {"claim_id": "one", "claim": claimed.to_dict()}
            worker.save()
            with patch("time.time", return_value=self.now / 1000):
                worker.cycle()
            self.assertEqual(worker.cycle().processing.reason_code, "SHADOW_ONLY")
            with self.store.connection() as c:
                self.assertEqual(c.execute("SELECT count(*) FROM gateway_datasets").fetchone()[0], 2)
                manifest = json.loads(c.execute("SELECT payload FROM accepted_manifests").fetchone()[0])
            self.assertEqual(len(manifest["requests"]), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_hard_exit_after_query_commit_replays_evidence(self):
        claimed = self.claim()
        message = self.query(claimed)
        code = '''
import os,sys,json
from perpetual_alpha.runtime.store import RuntimeStore
from perpetual_alpha.runtime.jobs import JobQueue
from perpetual_alpha.runtime.ingress import Principal
from perpetual_alpha.historical.store import HistoricalStore
from perpetual_alpha.transport.data_gateway import DataGateway
from perpetual_alpha.transport.messages import DataQueryRequest
q=JobQueue(RuntimeStore(sys.argv[1],clock=lambda:1200008))
g=DataGateway(q,HistoricalStore(sys.argv[2],read_only=True))
g.query(Principal.from_dict(json.loads(sys.argv[3])),DataQueryRequest.from_dict(json.loads(sys.argv[4])))
os._exit(75)
'''
        result = subprocess.run([sys._base_executable, "-c", code, str(self.store.path), str(self.history.path),
                                 canonical(self.principal.to_dict()), canonical(message.to_dict())], capture_output=True,
                                timeout=10, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
        self.assertEqual(result.returncode, 75, result.stderr.decode())
        with patch.object(self.gateway.history, "read_candles", side_effect=AssertionError("must replay")):
            self.assertEqual(self.gateway.query(self.principal, message).status, "ready")

    def test_migrate_runtime_v2_retains_pr2_rows(self):
        path = self.folder / "v2.db"
        import sqlite3
        c = sqlite3.connect(path)
        for name in ("001_runtime.sql", "002_mock_http.sql"):
            c.executescript((ROOT / "src/perpetual_alpha/runtime/migrations" / name).read_text())
        c.execute("PRAGMA application_id=1346456113")
        c.execute("PRAGMA user_version=2")
        c.execute("INSERT INTO mock_configurations VALUES('kept','digest','{}')")
        c.commit()
        c.close()
        migrated = RuntimeStore(path)
        with migrated.connection() as c:
            self.assertEqual(c.execute("SELECT configuration_id FROM mock_configurations").fetchone()[0], "kept")
            self.assertEqual(c.execute("SELECT count(*) FROM gateway_datasets").fetchone()[0], 0)


class GatewayProcessTests(unittest.TestCase):
    def test_core_and_worker_restart_between_http_query_rounds(self):
        import socket
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            history = HistoricalStore(path / "history.db")
            end = history.clock() // 60000 * 60000
            for symbol in ("par:BTC", "par:ETH"):
                bars = [{"s": symbol, "i": "1m", "t": t, "T": t + 59999, "o": "100", "h": "110", "l": "90",
                         "c": "105", "v": "1", "n": 2} for t in range(end - 1800000, end, 60000)]
                history.record_candles("synthetic-process-test:" + symbol, symbol, "1m", end - 1800000, end, bars, history.clock())
            config = path / "config.toml"
            config.write_text((ROOT / "config/mock-http.toml").read_text().replace("interval_ms = 60000", "interval_ms = 1000"))
            # The first scheduled cutoff must follow prewarming; 1ms ticks can never reliably span SQLite transactions.
            time.sleep(((history.clock() // 1000 + 1) * 1000 - history.clock()) / 1000 + .05)
            creds, tokenfile = path / "credentials.json", path / "token"
            tokenfile.write_text(TOKEN)
            creds.write_text(json.dumps([{"token": TOKEN, "worker_id": "process-worker", "producer_id": "dummy-agent",
                                         "producer_version": "offline-0.1", "role": "shadow"}]))
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            command = [sys._base_executable, "-u", "-m", "perpetual_alpha.runtime.http_core", "--config", str(config),
                       "--credentials", str(creds), "--database", str(path / "runtime.db"), "--journal", str(path / "mock.db"),
                       "--history-database", str(history.path), "--port", str(port)]
            log = (path / "core.log").open("w+")
            core = None
            try:
                def start():
                    process = subprocess.Popen(command, env=env, cwd=ROOT, stdout=log, stderr=log)
                    until = time.monotonic() + 10
                    while time.monotonic() < until:
                        if process.poll() is not None:
                            log.seek(0)
                            raise AssertionError(log.read())
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                                return process
                        except OSError:
                            time.sleep(.02)
                    process.kill()
                    process.wait(5)
                    raise AssertionError("Core startup timeout")

                core = start()
                code = '''
import os,sys
from perpetual_alpha.transport.history_worker import HistoricalDummyWorker
from perpetual_alpha.transport.http_client import HTTPClient
from perpetual_alpha.runtime.mock_config import load_config
client=HTTPClient(sys.argv[1],sys.argv[2])
original=client.call
def crash(path,cls,message=None):
    response=original(path,cls,message)
    if path.endswith('/data/query'): os._exit(76)
    return response
client.call=crash
HistoricalDummyWorker(client,sys.argv[3],load_config(sys.argv[4])).run(once=True)
'''
                url = "http://127.0.0.1:" + str(port)
                state = path / "worker.json"
                result = subprocess.run([sys._base_executable, "-c", code, url, TOKEN, str(state), str(config)],
                                        env=env, cwd=ROOT, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 76, result.stderr.decode())
                core.kill()
                core.wait(5)
                core = start()
                result = subprocess.run([sys._base_executable, "-m", "perpetual_alpha.transport.dummy_worker", "--history",
                                         "--core-url", url, "--token-file", str(tokenfile), "--state", str(state),
                                         "--config", str(config), "--once"], env=env, cwd=ROOT, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                output = json.loads(result.stdout)
                self.assertEqual(output["feedback"]["processing"]["reason_code"], "SHADOW_ONLY")
                c = sqlite3.connect(path / "runtime.db")
                try:
                    self.assertEqual(c.execute("SELECT count(*) FROM gateway_datasets").fetchone()[0], 2)
                    responses = c.execute("SELECT response FROM gateway_datasets").fetchall()
                    self.assertTrue(all(json.loads(row[0])["status"] == "ready" for row in responses))
                    self.assertEqual(c.execute("SELECT count(*) FROM gateway_reads").fetchone()[0], 2)
                finally:
                    c.close()
                c = sqlite3.connect(path / "mock.db")
                try:
                    self.assertEqual(c.execute("SELECT count(*) FROM decisions").fetchone()[0], 0)
                finally:
                    c.close()
            finally:
                if core is not None and core.poll() is None:
                    core.kill()
                    core.wait(5)
                log.close()
