"""Isolated Docker acceptance. Optional retained VPS project always finishes paused."""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", help="New private evidence/credential directory; retained, never overwritten")
    p.add_argument("--soak-seconds", type=int, default=60)
    p.add_argument("--keep", action="store_true", help="Keep isolated paused deployment; requires --directory")
    args = p.parse_args()
    if not 60 <= args.soak_seconds <= 86400 or (args.keep and not args.directory):
        p.error("Bounded 60..86400 second soak; keep requires a persistent directory")
    temporary = tempfile.TemporaryDirectory(prefix="paper-ops-") if not args.directory else None
    folder = Path(temporary.name if temporary else args.directory).resolve()
    if not temporary:
        folder.mkdir(mode=0o700)  # Must be new; protects prior evidence and deployment credentials.
    credentials = folder/"secrets"
    credentials.mkdir(mode=0o700)
    token = secrets.token_urlsafe(48)
    (credentials/"core-auth.json").write_text(json.dumps([{"token": token, "worker_id": "paper-ops-worker",
                                                        "producer_id": "dummy-agent", "producer_version": "offline-0.1", "role": "control"}]), encoding="utf-8")
    (credentials/"worker-token").write_text(token, encoding="utf-8")
    # Directory is 0700 on host; Compose file-backed secrets must be readable by container UID1000.
    for secret in credentials.iterdir():
        secret.chmod(0o444)
    project = "paper-ops-"+uuid4().hex[:12]
    env = {**os.environ, "PAPER_IMAGE": os.environ.get("PAPER_IMAGE", "perpetual-alpha-paper:ci"),
           "PAPER_SECRET_DIR": str(credentials)}
    base = ["docker", "compose", "-p", project, "-f", str(ROOT/"deploy/compose.paper.yaml")]
    (folder/"project.json").write_text(json.dumps({"project": project, "image": env["PAPER_IMAGE"],
                                                "compose": str(ROOT/"deploy/compose.paper.yaml")}), encoding="utf-8")

    def run(*command, timeout=60):
        r = subprocess.run([*base, *command], env=env, capture_output=True, text=True, timeout=timeout)
        if r.returncode:
            raise RuntimeError("Compose operation failed: "+" ".join(command[:4])+"\n"+r.stderr[-2000:])
        return r.stdout

    def execute(*command):
        return run("exec", "-T", "core", *command)

    def snapshot():
        return json.loads(execute("python", "-m", "perpetual_alpha.runtime.paper_ops", "--database", "/data/runtime.sqlite3", "inspect"))

    def await_boot(old=None):
        until = time.monotonic()+45
        while time.monotonic() < until:
            try:
                state = snapshot()
                execute("python", "-m", "perpetual_alpha.runtime.paper_health")
                if state["generation"] != old and state["generation"] != "not-booted" and state["paused"]:
                    return state
            except (RuntimeError, json.JSONDecodeError):
                pass
            time.sleep(.5)
        raise AssertionError("Core did not boot paused with fresh loop progress")

    def resume(state):
        return execute("python", "-m", "perpetual_alpha.runtime.paper_operator", "--database", "/data/runtime.sqlite3",
                       "resume", "--generation", state["generation"], "--pause-epoch", str(state["pause_epoch"]),
                       "--request-id", "ops-"+uuid4().hex, "--actor", "synthetic-ops-acceptance")

    report = {"mode": "synthetic-paper", "real_execution_authorized": False, "project": project,
              "image": env["PAPER_IMAGE"], "started_ms": time.time_ns()//1000000, "samples": []}

    def save_report():
        pending = folder/"report.json.tmp"
        pending.write_text(json.dumps(report, indent=2), encoding="utf-8")
        os.replace(pending, folder/"report.json")

    try:
        run("up", "-d", "--wait", timeout=90)
        state = await_boot()
        # HTTP readiness is still 503, while loop liveness is healthy and Worker can start/back off.
        execute("python", "-c", "import urllib.request,urllib.error;\ntry: urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2)\nexcept urllib.error.HTTPError as e: assert e.code==503\nelse: raise AssertionError('paused Core ready')")
        resume(state)
        started = time.monotonic()
        while time.monotonic()-started < args.soak_seconds:
            check_start = time.monotonic()
            state = snapshot()
            state["inspect_latency_ms"] = round((time.monotonic()-check_start)*1000, 2)
            state["elapsed_seconds"] = round(time.monotonic()-started, 1)
            state["loop"] = json.loads(execute("python", "-m", "perpetual_alpha.runtime.paper_health"))
            state["host_free_bytes"] = shutil.disk_usage(folder).free
            ids = run("ps", "-q").split()
            stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids],
                                   capture_output=True, text=True, check=True, timeout=15)
            state["resources"] = [json.loads(line) for line in stats.stdout.splitlines()]
            report["samples"].append(state)
            save_report()
            if state["paused"]:
                raise AssertionError("Unexpected pause during synthetic soak")
            time.sleep(min(10, max(0, args.soak_seconds-(time.monotonic()-started))))
        assert snapshot()["states"]["paper_plans"].get("completed", 0) >= 1
        report["soak_seconds"] = round(time.monotonic()-started, 1)
        worker = run("ps", "-q", "worker").strip()
        details = json.loads(subprocess.run(["docker", "inspect", worker], capture_output=True, text=True, check=True).stdout)[0]
        network = next(iter(details["NetworkSettings"]["Networks"]))
        subprocess.run(["docker", "network", "disconnect", network, worker], check=True, timeout=15)
        try:
            run("exec", "-T", "worker", "python", "-c",
                "import socket;\ntry: socket.create_connection(('core',8080),timeout=2)\nexcept OSError: pass\nelse: raise AssertionError('network fault not applied')")
            time.sleep(6)  # Worker must retain local state and back off through actual network loss.
        finally:
            subprocess.run(["docker", "network", "connect", network, worker], check=True, timeout=15)
        run("stop", "worker")
        run("start", "worker")
        run("kill", "-s", "SIGKILL", "core")
        run("up", "-d", "core")
        restarted = await_boot(state["generation"])
        run("stop", "worker")  # No claims during final backup; Core remains healthy and paused.
        execute("python", "-m", "perpetual_alpha.runtime.paper_ops", "--database", "/data/runtime.sqlite3",
                "backup", "--destination", "/data/acceptance-backup.sqlite3")
        result = json.loads(execute("python", "-m", "perpetual_alpha.runtime.paper_ops", "--database",
                                    "/data/acceptance-backup.sqlite3", "verify"))
        assert result["restore_verified"] and result["restored_boot_paused"]
        before = snapshot()["counts"]["paper_fills"]
        time.sleep(2)
        assert snapshot()["counts"]["paper_fills"] == before
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
                        os.environ.get("PAPER_CHECK_IMAGE", "perpetual-alpha-checks:ci"), "python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_paper_ops.py", "-v"],
                       check=True, timeout=60)
        # Access boundaries, non-root/read-only filesystem, no public publishing or external egress.
        run("run", "--rm", "--no-deps", "worker", "python", "-c",
            "import os,pathlib,socket; assert os.getuid()==1000; "
            "assert not pathlib.Path('/data/runtime.sqlite3').exists(); assert not pathlib.Path('/run/secrets/core-auth').exists(); "
            "\ntry: pathlib.Path('/app/ops-write-test').write_text('x')\nexcept OSError: pass\nelse: raise AssertionError('writable root')"
            "\ntry: socket.create_connection(('1.1.1.1',443),timeout=2)\nexcept OSError: pass\nelse: raise AssertionError('external egress')")
        ids = run("ps", "-a", "-q").split()
        configs = json.loads(subprocess.run(["docker", "inspect", *ids], capture_output=True, text=True, check=True, timeout=15).stdout)
        network_info = json.loads(subprocess.run(["docker", "network", "inspect", network], capture_output=True,
                                                 text=True, check=True, timeout=15).stdout)[0]
        assert network_info["Internal"]
        for container in configs:
            assert not container["HostConfig"]["PortBindings"]
            assert container["HostConfig"]["ReadonlyRootfs"]
            assert container["HostConfig"]["Memory"] > 0 and container["HostConfig"]["NanoCpus"] > 0
            assert container["HostConfig"]["LogConfig"]["Config"]["max-file"] == "3"
        core = next(container for container in configs if container["Config"]["Labels"]["com.docker.compose.service"] == "core")
        volume = next(mount["Name"] for mount in core["Mounts"] if mount["Destination"] == "/data")
        readonly = subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
                                   "--mount", f"type=volume,source={volume},target=/data,readonly", env["PAPER_IMAGE"],
                                   "python", "-m", "perpetual_alpha.runtime.paper_operator", "--database", "/data/runtime.sqlite3", "status"],
                                  capture_output=True, text=True, timeout=20)
        assert readonly.returncode != 0 and ("readonly" in readonly.stderr.lower() or "read-only" in readonly.stderr.lower())
        report.update(passed=True, completed_ms=time.time_ns()//1000000, acceptance_seconds=round(time.monotonic()-started, 1),
                      final=snapshot(), backup=result, restarted_generation=restarted["generation"],
                      checks=["paused-liveness", "worker-network-loss-reconnect", "worker-restart", "core-SIGKILL-paused", "online-WAL-backup",
                              "restore-ledger-paused", "no-extra-fill-while-paused", "worker-no-core-db-or-secret",
                              "nonroot-readonly", "readonly-state-volume-refused", "no-egress-or-published-ports",
                              "resource-and-log-limits", "bounded-SQLITE_FULL"])
        run("cp", "core:/data/acceptance-backup.sqlite3", str(folder/"acceptance-backup.sqlite3"))
        print(json.dumps({k: v for k, v in report.items() if k not in ("samples", "backup", "final")}))
    finally:
        save_report()
        if args.keep:
            # Failure also stops both; never leave a failed acceptance running with permission.
            if not report.get("passed"):
                run("stop")
        else:
            run("down", "--volumes", "--remove-orphans")  # Only this random disposable test project.
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    main()
