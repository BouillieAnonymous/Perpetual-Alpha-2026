"""Offline Paper acceptance with actual SQLite/Outbox; only explicitly synthetic inputs."""
import json
import tempfile
from pathlib import Path

from perpetual_alpha.contracts import Forecast, ForecastBatch
from perpetual_alpha.runtime.contracts import AnalysisJob, JobLimits
from perpetual_alpha.runtime.ingress import Principal
from perpetual_alpha.runtime.jobs import JobQueue
from perpetual_alpha.runtime.mock_config import load_config
from perpetual_alpha.runtime.mock_fixture import context_for, initial_account
from perpetual_alpha.runtime.paper_engine import PaperEngine
from perpetual_alpha.runtime.store import RuntimeStore
from perpetual_alpha.transport.messages import ClaimRequest, ForecastSubmission


def main():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root/"config/paper-execution.synthetic.json").read_text(encoding="utf-8"))
    sim = load_config(root/"config/mock-http.toml")["simulation"]
    now = [1000000]
    with tempfile.TemporaryDirectory() as folder:
        store = RuntimeStore(Path(folder)/"runtime.sqlite3", clock=lambda: now[0])
        engine, queue = PaperEngine(store, config), JobQueue(store)
        principal = Principal("paper-validator", "dummy-agent", "offline-0.1", "control")
        queue.configure_control(config["scope"], principal.producer_id, principal.producer_version, 1)
        queue.configure_worker(principal)
        engine.boot()
        status = engine.status()
        assert status["paused"]
        engine.resume("ci-explicit-resume", status["generation"], status["pause_epoch"], "synthetic-ci")
        job = AnalysisJob("paper-ci-job", "paper-ci-context", principal.producer_id, principal.producer_version, config["scope"],
                          1, "control", tuple(sim["quantities"]), now[0], now[0], now[0]+120000, "queued", 0, 0,
                          JobLimits(10,100,262144,2,90000,20000))
        ctx, req = context_for(sim, job.context_id, job.asof_ms, initial_account(sim, job.asof_ms))
        ctx["account"] = None
        queue.enqueue(job, ctx, req)
        claim = queue.claim(principal, ClaimRequest("paper-ci-claim"), lease_ms=30000)
        batch = ForecastBatch("paper-ci-batch", job.context_id, job.producer_id, job.producer_version, job.asof_ms,
                              tuple(Forecast(s, job.asof_ms, job.asof_ms+90000, sim["horizon_ms"], "ready", "up", ("fixture-quotes",))
                                    for s in job.symbols))
        queue.submit(principal, ForecastSubmission("paper-ci-submit", claim.lease.ref, batch))
        for _ in range(10):
            now[0] += 1
            engine.publish_fixture()
            engine.step()
        feedback = queue.feedback(principal, job.job_id)
        assert feedback.processing.status == "completed"
        engine.verify_ledger()
        with store.connection() as c:
            assert c.execute("SELECT count(*) FROM paper_plans").fetchone()[0] == 1
            assert c.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 3
            assert c.execute("SELECT count(*) FROM mock_plans").fetchone()[0] == 0
        engine.boot()
        assert engine.status()["paused"]
    print("Synthetic Paper intents, partial fills, fees, atomic Outbox feedback and paused restart verified; no trading")


if __name__ == "__main__":
    main()
