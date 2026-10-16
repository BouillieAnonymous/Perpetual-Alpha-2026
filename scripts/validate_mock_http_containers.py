"""Disposable two-container acceptance test; no published ports, signing keys or external services."""
import argparse
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true", help="Shadow historical Gateway; empty local history is explicit missing")
    args = parser.parse_args()
    project = "pa-http-test-" + uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=project) as folder:
        directory = Path(folder)
        token = secrets.token_urlsafe(48)
        (directory / "worker-token").write_text(token, encoding="utf-8")
        (directory / "core-auth.json").write_text(json.dumps([{
            "token": token, "worker_id": "container-worker", "producer_id": "dummy-agent",
            "producer_version": "offline-0.1", "role": "shadow" if args.history else "control"}]), encoding="utf-8")
        environment = {**os.environ, "MOCK_HTTP_SECRET_DIR": str(directory),
                       "MOCK_HTTP_IMAGE": "perpetual-alpha-mock-http:ci"}
        command = ["docker", "compose", "-f", str(ROOT / "deploy/compose.mock-http.yaml"), "-p", project]
        if args.history:
            override = directory / "history-compose.json"
            override.write_text(json.dumps({"services": {"core": {"command": [
                "python", "-u", "-m", "perpetual_alpha.runtime.http_core", "--host", "0.0.0.0",
                "--credentials", "/run/secrets/core-auth", "--database", "/data/runtime.sqlite3",
                "--journal", "/data/mock.sqlite3", "--history-database", "/data/history.sqlite3"]}}}), encoding="utf-8")
            command += ["-f", str(override)]

        def run(*args, timeout=90):
            result = subprocess.run([*command, *args], env=environment, cwd=ROOT,
                                    text=True, capture_output=True, timeout=timeout)
            if result.returncode:
                raise RuntimeError(f"Compose {args[0]} failed: {result.stderr}")
            return result.stdout

        try:
            if args.history:
                run("run", "--rm", "--no-deps", "core", "python", "-c",
                    "from perpetual_alpha.historical.store import HistoricalStore; HistoricalStore('/data/history.sqlite3')")
            run("up", "-d", "--wait", "core")
            worker = ["run", "--rm", "--no-deps", "worker", "python", "-m",
                         "perpetual_alpha.transport.dummy_worker", "--core-url", "http://core:8080",
                         "--token-file", "/run/secrets/worker-token", "--state", "/state/worker.json", "--once"]
            if args.history:
                worker.append("--history")
            output = run(*worker)
            feedback = json.loads(output)["feedback"]
            if feedback["processing"]["status"] != "completed" or (not args.history and not feedback["processing"]["report_id"]):
                raise AssertionError("Container loop did not complete")
            if args.history and feedback["processing"]["reason_code"] != "SHADOW_ONLY":
                raise AssertionError("Historical demo must stay shadow")
            # Restart the Core against its volume and verify the same single immutable Mock Decision survives.
            run("restart", "core")
            run("up", "-d", "--wait", "core")
            validation = (
                         "import sqlite3,json; c=sqlite3.connect('/data/runtime.sqlite3'); "
                         "rows=c.execute('SELECT response FROM gateway_datasets').fetchall(); "
                         "assert len(rows)==2; assert all(json.loads(r[0])['status']=='missing' for r in rows); "
                         "assert c.execute('SELECT count(*) FROM accepted_manifests').fetchone()[0]==1; c.close(); "
                         "c=sqlite3.connect('/data/mock.sqlite3'); assert c.execute('SELECT count(*) FROM decisions').fetchone()[0]==0; "
                         "print('Shadow Gateway: two rounds, explicit missing, sealed manifest, zero Mock Decisions after restart')"
                         if args.history else
                         "import sqlite3,json; c=sqlite3.connect('/data/mock.sqlite3'); "
                         "rows=c.execute('SELECT report FROM decisions').fetchall(); "
                         "assert len(rows)==1; assert json.loads(rows[0][0])['status']=='completed'; "
                         "print('Mock Decision survived Core container restart')")
            output = run("exec", "-T", "core", "python", "-c", validation)
            print(output.strip())
            print(json.dumps({"mode": "history-shadow" if args.history else "mock-http", "simulated": True, "two_containers": "passed",
                              "job_id": feedback["job_id"], "processing": feedback["processing"]["status"]}))
        finally:
            # Only this randomly named project's disposable test volumes/network are removed.
            run("down", "--volumes", "--remove-orphans", timeout=45)


if __name__ == "__main__":
    main()
