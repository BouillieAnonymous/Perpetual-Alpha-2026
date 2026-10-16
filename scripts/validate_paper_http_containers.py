"""Disposable Paper Core/Dummy containers, explicit local resume and paused durable restart."""
import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    project = "pa-paper-test-"+uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=project) as folder:
        directory = Path(folder)
        token = secrets.token_urlsafe(48)
        (directory/"worker-token").write_text(token, encoding="utf-8")
        (directory/"core-auth.json").write_text(json.dumps([{"token": token, "worker_id": "paper-container-worker",
                                                           "producer_id": "dummy-agent", "producer_version": "offline-0.1", "role": "control"}]), encoding="utf-8")
        override = directory/"paper-compose.json"
        override.write_text(json.dumps({"services": {"core": {"command": ["python", "-u", "-m", "perpetual_alpha.runtime.paper_core",
                            "--host", "0.0.0.0", "--credentials", "/run/secrets/core-auth", "--database", "/data/paper-runtime.sqlite3"]}}}), encoding="utf-8")
        command = ["docker", "compose", "-f", str(ROOT/"deploy/compose.mock-http.yaml"), "-f", str(override), "-p", project]
        env = {**os.environ, "MOCK_HTTP_SECRET_DIR": str(directory), "MOCK_HTTP_IMAGE": "perpetual-alpha-mock-http:ci"}

        def run(*args, timeout=90):
            result = subprocess.run([*command, *args], cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
            if result.returncode:
                raise RuntimeError("Paper Compose "+args[0]+" failed: "+result.stderr)
            return result.stdout

        operator = ["exec", "-T", "core", "python", "-m", "perpetual_alpha.runtime.paper_operator", "--database", "/data/paper-runtime.sqlite3"]

        def status(previous=None):
            until = time.monotonic()+40
            while time.monotonic() < until:
                try:
                    result = json.loads(run(*operator, "status", timeout=10))
                    if result["generation"] != "not-booted" and result["generation"] != previous:
                        return result
                except (RuntimeError, json.JSONDecodeError):
                    pass
                time.sleep(.25)
            raise AssertionError("Paper Core did not initialize a new paused boot")

        try:
            run("up", "-d", "core")  # Startup is intentionally HTTP-unready until explicit operator resume.
            before = status()
            assert before["paused"]
            run(*operator, "resume", "--generation", before["generation"], "--pause-epoch", str(before["pause_epoch"]),
                "--request-id", "container-explicit-resume", "--actor", "synthetic-ci")
            run("up", "-d", "--wait", "core")
            output = run("run", "--rm", "--no-deps", "worker", "python", "-m", "perpetual_alpha.transport.dummy_worker",
                         "--core-url", "http://core:8080", "--token-file", "/run/secrets/worker-token", "--state", "/state/worker.json", "--once")
            feedback = json.loads(output)["feedback"]
            assert feedback["processing"]["status"] == "completed"
            assert feedback["processing"]["report_id"].startswith("paper-decision:")
            run("restart", "core")
            after = status(before["generation"])
            assert after["paused"]
            check = ("import sqlite3,json; c=sqlite3.connect('/data/paper-runtime.sqlite3'); "
                     "assert c.execute('SELECT count(*) FROM paper_plans').fetchone()[0]==1; "
                     "assert c.execute('SELECT count(*) FROM paper_fills').fetchone()[0]==3; "
                     "assert c.execute('SELECT count(*) FROM mock_plans').fetchone()[0]==0; "
                     "assert json.loads(c.execute('SELECT report FROM paper_plans').fetchone()[0])['status']=='completed'; "
                     "print('Paper ledger and terminal Processing survived restart without extra fills')")
            print(run("exec", "-T", "core", "python", "-c", check).strip())
            print(json.dumps({"mode": "synthetic-paper", "two_containers": "passed", "restart_paused": True,
                              "processing": feedback["processing"]["status"], "real_execution_authorized": False}))
        finally:
            # Only this random CI project's disposable volumes/network are removed.
            run("down", "--volumes", "--remove-orphans", timeout=45)


if __name__ == "__main__":
    main()
