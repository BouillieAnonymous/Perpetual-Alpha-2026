"""Separate Paper Core and Dummy processes over real localhost HTTP; no external services."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from paper_support import configuration

from perpetual_alpha.runtime.paper_engine import PaperEngine
from perpetual_alpha.runtime.store import RuntimeStore

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT/"src"), "PYTHONUTF8": "1"}
TOKEN = "synthetic-paper-token-not-a-trading-key-123456789"


class PaperCoreTests(unittest.TestCase):
    def test_independent_http_worker_feedback_and_restart_paused(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            config = configuration()
            cfg = directory/"paper.json"
            cfg.write_text(json.dumps(config), encoding="utf-8")
            credentials, token = directory/"credentials.json", directory/"token"
            credentials.write_text(json.dumps([{"token": TOKEN, "worker_id": "paper-process-worker",
                                                "producer_id": "dummy-agent", "producer_version": "offline-0.1", "role": "control"}]), encoding="utf-8")
            token.write_text(TOKEN, encoding="utf-8")
            database = directory/"runtime.sqlite3"
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            command = [sys._base_executable, "-u", "-m", "perpetual_alpha.runtime.paper_core", "--credentials", str(credentials),
                       "--database", str(database), "--paper-config", str(cfg), "--port", str(port)]
            with (directory/"core.log").open("w+", encoding="utf-8") as log:
                def start(previous=None):
                    core = subprocess.Popen(command, stdout=log, stderr=log, cwd=ROOT, env=ENV)
                    try:
                        until = time.monotonic()+20
                        while time.monotonic() < until:
                            if core.poll() is not None:
                                log.seek(0)
                                self.fail(log.read())
                            try:
                                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=.3).close()
                            except urllib.error.HTTPError as e:
                                if e.code != 503:
                                    raise
                            except OSError:
                                time.sleep(.05)
                                continue
                            engine = PaperEngine(RuntimeStore(database), config)
                            status = engine.status()
                            if status["generation"] != "not-booted" and status["generation"] != previous:
                                return core, engine, status
                            time.sleep(.05)
                        self.fail("Paper Core startup timeout")
                    except BaseException:
                        core.kill()
                        core.wait(5)
                        raise

                core, engine, status = start()
                try:
                    self.assertTrue(status["paused"])
                    engine.resume("process-resume", status["generation"], status["pause_epoch"], "synthetic-process-test")
                    result = subprocess.run([sys._base_executable, "-m", "perpetual_alpha.transport.dummy_worker", "--core-url",
                                             f"http://127.0.0.1:{port}", "--token-file", str(token), "--state", str(directory/"worker.json"), "--once"],
                                            cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=35)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    feedback = json.loads(result.stdout)["feedback"]
                    self.assertEqual(feedback["processing"]["status"], "completed")
                    self.assertTrue(feedback["processing"]["report_id"].startswith("paper-decision:"))
                finally:
                    core.terminate()
                    core.wait(10)
                before_generation = status["generation"]
                core, engine, status = start(before_generation)
                try:
                    self.assertTrue(status["paused"])
                    engine.verify_ledger()
                    with engine.store.connection() as c:
                        self.assertEqual(c.execute("SELECT count(*) FROM paper_plans").fetchone()[0], 1)
                        self.assertEqual(c.execute("SELECT count(*) FROM paper_fills").fetchone()[0], 3)
                        self.assertEqual(c.execute("SELECT count(*) FROM mock_plans").fetchone()[0], 0)
                finally:
                    core.terminate()
                    core.wait(10)


if __name__ == "__main__":
    unittest.main()
