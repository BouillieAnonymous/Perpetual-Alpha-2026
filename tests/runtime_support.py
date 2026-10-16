"""Synthetic, unsigned fixtures for real SQLite runtime tests."""
from perpetual_alpha.contracts import Forecast, ForecastBatch
from perpetual_alpha.interfaces import DataRequest
from perpetual_alpha.runtime.contracts import AnalysisJob, JobLimits
from perpetual_alpha.runtime.ingress import Principal
from perpetual_alpha.runtime.jobs import JobQueue
from perpetual_alpha.runtime.store import RuntimeStore
from perpetual_alpha.transport.messages import ClaimRequest, ForecastSubmission


class Clock:
    def __init__(self, now=100000):
        self.now = now

    def __call__(self):
        return self.now


def setup_runtime(path):
    clock = Clock()
    store = RuntimeStore(path, clock=clock)
    queue = JobQueue(store)
    principal = Principal("worker-a", "dummy-agent", "test-0.1", "control")
    queue.configure_control("paper", principal.producer_id, principal.producer_version, 1)
    queue.configure_worker(principal)
    return clock, store, queue, principal


def job(name="one", *, asof=100000, created=100000, scope="paper", epoch=1, role="control"):
    return AnalysisJob("job-" + name, "ctx-" + name, "dummy-agent", "test-0.1", scope, epoch, role,
                       ("par:BTC",), asof, created, asof + 120000, "queued", 0, 0,
                       JobLimits(10, 100, 262144, 2, 90000, 20000))


def manifest(item):
    request = DataRequest("btc-quotes", "hyperliquid-testnet/par", "quote", ("par:BTC",),
                          ("bid", "ask"), 0, 1, 90000)
    context = {"protocol_version": "1.0", "context_id": item.context_id, "asof_ms": item.asof_ms,
               "account": None, "execution_reports": [], "datasets": {"btc-quotes": {
                   "source": request.source, "kind": request.kind, "parameters": {}, "required": True,
                   "status": "ready", "records": [{"symbol": "par:BTC", "event_ms": item.asof_ms - 1000,
                   "received_ms": item.asof_ms - 500, "data": {"bid": "99", "ask": "101"}}]}}}
    return context, (request,)


def enqueue(queue, item=None):
    item = item or job()
    context, requests = manifest(item)
    return queue.enqueue(item, context, requests)


def claim(queue, principal, request_id="claim-one"):
    return queue.claim(principal, ClaimRequest(request_id), lease_ms=30000)


def submission(claimed, *, submission_id="submit-one", batch_id="batch-one", direction="up", created=108000):
    item = claimed.job
    batch = ForecastBatch(batch_id, item.context_id, item.producer_id, item.producer_version, created,
                          (Forecast("par:BTC", item.asof_ms, item.asof_ms + 90000, 3600000,
                                    "ready", direction, ("btc-quotes",)),))
    return ForecastSubmission(submission_id, claimed.lease.ref, batch)
