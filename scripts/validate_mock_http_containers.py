"""Disposable two-container acceptance test; no published ports, signing keys or external services."""
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    project = "pa-http-test-" + uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=project) as folder:
        directory = Path(folder)
        token = secrets.token_urlsafe(48)
        (directory / "worker-token").write_text(token, encoding="utf-8")
        (directory / "core-auth.json").write_text(json.dumps([{
            "token": token, "worker_id": "container-worker", "producer_id": "dummy-agent",
            "producer_version": "offline-0.1", "role": "control"}]), encoding="utf-8")
        environment = {**os.environ, "MOCK_HTTP_SECRET_DIR": str(directory),
                       "MOCK_HTTP_IMAGE": "perpetual-alpha-mock-http:ci"}
        command = ["docker", "compose", "-f", str(ROOT / "deploy/compose.mock-http.yaml"), "-p", project]

        def run(*args, timeout=90):
            result = subprocess.run([*command, *args], env=environment, cwd=ROOT,
                                    text=True, capture_output=True, timeout=timeout)
            if result.returncode:
                raise RuntimeError(f"Compose {args[0]} failed: {result.stderr}")
            return result.stdout

        try:
            run("up", "-d", "--wait", "core")
            output = run("run", "--rm", "--no-deps", "worker", "python", "-m",
                         "perpetual_alpha.transport.dummy_worker", "--core-url", "http://core:8080",
                         "--token-file", "/run/secrets/worker-token", "--state", "/state/worker.json", "--once")
            feedback = json.loads(output)["feedback"]
            if feedback["processing"]["status"] != "completed" or not feedback["processing"]["report_id"]:
                raise AssertionError("Container loop did not complete")
            # Restart the Core against its volume and verify the same single immutable Mock Decision survives.
            run("restart", "core")
            run("up", "-d", "--wait", "core")
            output = run("exec", "-T", "core", "python", "-c",
                         "import sqlite3,json; c=sqlite3.connect('/data/mock.sqlite3'); "
                         "rows=c.execute('SELECT report FROM decisions').fetchall(); "
                         "assert len(rows)==1; assert json.loads(rows[0][0])['status']=='completed'; "
                         "print('Mock Decision survived Core container restart')")
            print(output.strip())
            print(json.dumps({"mode": "mock-http", "simulated": True, "two_containers": "passed",
                              "job_id": feedback["job_id"], "processing": feedback["processing"]["status"]}))
        finally:
            # Only this randomly named project's disposable test volumes/network are removed.
            run("down", "--volumes", "--remove-orphans", timeout=45)


if __name__ == "__main__":
    main()
