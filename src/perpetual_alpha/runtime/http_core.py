"""Independent synthetic Trading Core entrypoint; no live execution backend option."""
import argparse
import json
import signal
import threading
from contextlib import ExitStack
from pathlib import Path

from ..journal import Journal, canonical
from ..transport.http_server import AgentHTTPServer, load_credentials
from .contracts import JobLimits
from .jobs import JobQueue
from .mock_config import load_config
from .mock_consumer import MockConsumer
from .mock_fixture import context_for, initial_account
from .process_lock import ProcessLock
from .scheduler import Schedule, Scheduler
from .store import RuntimeStore


def main():
    parser = argparse.ArgumentParser(description="Fixture-only HTTP Core; never sends exchange orders")
    parser.add_argument("--config", default="config/mock-http.toml")
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--history-database", help="Optional prewarmed history; enables shadow-only dynamic Jobs")
    args = parser.parse_args()
    config = load_config(args.config)
    service, simulation = config["service"], config["simulation"]
    credentials = load_credentials(args.credentials)
    if Path(args.database).resolve() == Path(args.journal).resolve():
        raise ValueError("Runtime and Mock journal must be separate files")
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    with ExitStack() as stack:
        for filename in sorted((args.database, args.journal), key=lambda p: str(Path(p).resolve())):
            stack.enter_context(ProcessLock(str(Path(filename).resolve()) + ".lock"))
        store, journal = RuntimeStore(args.database), Journal(args.journal)
        stack.callback(journal.close)
        queue = JobQueue(store)
        gateway, policy = None, None
        if args.history_database:
            from ..historical.contracts import PRICE_UNITS
            from ..historical.store import HistoricalStore
            from ..transport.data_gateway import DataGateway, DataPolicy
            if Path(args.history_database).resolve() in {Path(args.database).resolve(), Path(args.journal).resolve()}:
                raise ValueError("History must be a separate database")
            with store.connection() as c:
                if c.execute("SELECT 1 FROM jobs WHERE role='control' LIMIT 1").fetchone():
                    raise ValueError("Use a separate runtime database for the shadow historical demo")
            if journal.connection.execute("SELECT 1 FROM decisions LIMIT 1").fetchone():
                raise ValueError("Use a separate empty Mock journal for the shadow historical demo")
            gateway = DataGateway(queue, HistoricalStore(args.history_database, read_only=True))
            policy = DataPolicy("history-shadow-demo/0.1", service["producer_id"], service["producer_version"],
                                "shadow", tuple(PRICE_UNITS), ("1m", "5m", "1h"),
                                ("open", "high", "low", "close", "volume", "trade_count"), 21600000)
            gateway.configure(policy)
            if not any((p.producer_id, p.producer_version, p.role) == (policy.producer_id, policy.producer_version, "shadow")
                       for _, p in credentials):
                raise ValueError("Historical demo requires a matching shadow Worker credential")
        queue.configure_control(service["control_scope"], service["producer_id"], service["producer_version"],
                                service["control_epoch"])
        # Credential file is the explicit startup allowlist, including revocations.
        with store.transaction() as (c, now):
            c.execute("UPDATE workers SET enabled=0")
        for _, principal in credentials:
            queue.configure_worker(principal)
        with journal.connection:
            journal.connection.execute("INSERT OR IGNORE INTO simulation_state VALUES(1,?)",
                                       (canonical(initial_account(simulation, store.clock())),))
        consumer = MockConsumer(store, journal, simulation)
        consumer.reconcile()
        consumer.outbox.recover()
        schedule = Schedule(service["schedule_id"], service["producer_id"], service["producer_version"],
                            service["control_scope"], service["control_epoch"], "shadow" if gateway else "control",
                            tuple(simulation["quantities"]), service["interval_ms"], service["deadline_ms"],
                            JobLimits(service["max_queries"], service["max_records"], service["max_bytes"],
                                      service["max_attempts"], service["max_input_age_ms"], service["max_inference_ms"]))
        server = AgentHTTPServer((args.host, args.port), queue, credentials, service, data_gateway=gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(canonical({"mode": "history-shadow" if gateway else "mock-http", "simulated": True, "port": server.server_port,
                         "status": "ready"}), flush=True)
        try:
            while not stopped.is_set():
                if consumer.unresolved():
                    server.ready = False
                    stopped.wait(service["poll_ms"] / 1000)
                    continue
                server.ready = True
                account = json.loads(journal.connection.execute("SELECT account FROM simulation_state WHERE id=1").fetchone()[0])
                if gateway:
                    Scheduler(queue).tick(schedule, lambda cid, asof: ({"protocol_version": "1.0", "context_id": cid,
                                          "asof_ms": asof, "account": None, "execution_reports": [], "datasets": {}}, ()),
                                          data_binding_factory=lambda asof: gateway.binding(policy, asof))
                else:
                    Scheduler(queue).tick(schedule, lambda cid, asof: context_for(simulation, cid, asof, account))
                if gateway is None:
                    consumer.step()
                stopped.wait(service["poll_ms"] / 1000)
        finally:
            server.ready = False
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
