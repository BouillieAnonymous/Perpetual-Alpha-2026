"""Synthetic Paper Core, existing HTTP Pull and PR7 risk. No real venue backend option."""
import argparse
import json
import signal
import threading
from pathlib import Path

from ..journal import canonical
from ..transport.http_server import AgentHTTPServer, load_credentials
from .contracts import JobLimits
from .jobs import JobQueue
from .mock_config import load_config
from .mock_fixture import context_for, initial_account
from .paper_engine import PaperEngine
from .process_lock import ProcessLock
from .scheduler import Schedule, Scheduler
from .store import RuntimeStore


def load_paper(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(1024*1024+1)
    if len(raw) > 1024*1024:
        raise ValueError("Paper fixture size exceeded")
    return json.loads(raw)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="config/mock-http.toml")
    p.add_argument("--paper-config", default="config/paper-execution.synthetic.json")
    p.add_argument("--credentials", required=True)
    p.add_argument("--database", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args()
    config, paper = load_config(args.config), load_paper(args.paper_config)
    service, simulation = config["service"], config["simulation"]
    template = paper["initial_state"]
    if (paper["scope"] != service["control_scope"] or template["control"]["producer_id"] != service["producer_id"]
            or template["control"]["producer_version"] != service["producer_version"]
            or set(template["control"]["symbols"]) != set(simulation["quantities"])
            or paper["policy"]["horizon_ms"] != simulation["horizon_ms"]
            or {s: q["usd_per_unit"] for s, q in template["market"].items()} != simulation["fixture_prices"]):
        raise ValueError("Paper/Worker fixture identity, universe, horizon or prices mismatch")
    credentials = load_credentials(args.credentials)
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    with ProcessLock(str(Path(args.database).resolve())+".lock"):
        store = RuntimeStore(args.database)
        engine = PaperEngine(store, paper)
        queue = JobQueue(store)
        queue.configure_control(service["control_scope"], service["producer_id"], service["producer_version"], service["control_epoch"])
        with store.transaction() as (c, now):
            c.execute("UPDATE workers SET enabled=0")
        for _, principal in credentials:
            queue.configure_worker(principal)
        engine.boot()
        schedule = Schedule("paper:"+service["schedule_id"], service["producer_id"], service["producer_version"],
                            service["control_scope"], service["control_epoch"], "control", tuple(template["control"]["symbols"]),
                            service["interval_ms"], service["deadline_ms"], JobLimits(service["max_queries"], service["max_records"],
                            service["max_bytes"], service["max_attempts"], service["max_input_age_ms"], service["max_inference_ms"]))
        server = AgentHTTPServer((args.host, args.port), queue, credentials, service)
        server.ready = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(canonical({"mode": "synthetic-paper", "port": server.server_port, "status": "paused", "simulated": True}), flush=True)

        def context(cid, asof):
            ctx, requests = context_for(simulation, cid, asof, initial_account(simulation, asof))
            ctx["account"] = None  # Do not backdate or disclose current Paper account into a frozen Agent context.
            return ctx, requests

        try:
            while not stopped.is_set():
                engine.publish_fixture()
                engine.step()
                status = engine.status()
                with store.connection() as c:
                    active = c.execute("SELECT 1 FROM paper_plans WHERE status IN ('running','unknown') LIMIT 1").fetchone()
                server.ready = not status["paused"] and not active
                if server.ready:
                    Scheduler(queue).tick(schedule, context)
                stopped.wait(service["poll_ms"]/1000)
        finally:
            server.ready = False
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    main()
