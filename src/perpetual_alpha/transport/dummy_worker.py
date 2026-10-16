"""A separate, restartable HTTP-only Dummy Agent. No Core database access or LLM SDK."""
import argparse
import json
import os
import threading
import time
import urllib.error
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from ..contracts import Forecast, ForecastBatch
from ..journal import canonical
from ..runtime.mock_config import load_config
from ..runtime.mock_fixture import request_for
from ..runtime.process_lock import ProcessLock
from .http_client import HTTPClient, RemoteError
from .messages import (
    ClaimRequest,
    ClaimResponse,
    DataQueryRequest,
    DataQueryResponse,
    FeedbackResponse,
    ForecastSubmission,
    HeartbeatRequest,
    HeartbeatResponse,
    SubmissionResponse,
)


class DummyWorker:
    mode = "mock-http"

    def __init__(self, client, state_path, config):
        self.client, self.path, self.config = client, Path(state_path), config
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(canonical(self.state))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        if os.name != "nt":
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def cycle(self):
        service = self.config["service"]
        if not self.state:
            self.state = {"claim_id": str(uuid4())}
            self.save()
        if "claim" not in self.state:
            claimed = self.client.call("/v1/jobs/claim", ClaimResponse, ClaimRequest(self.state["claim_id"]))
            if claimed is None:
                self.state = {}
                self.save()
                return None
            self.state["claim"] = claimed.to_dict()
            self.save()
        claimed = ClaimResponse.from_dict(self.state["claim"])
        self.validate_claim(claimed)
        route = "/v1/jobs/" + quote(claimed.job.job_id, safe="")
        feedback = self.client.call(route + "/feedback", FeedbackResponse)
        if feedback.receipt is not None:
            if feedback.processing.status in {"completed", "blocked", "failed"}:
                self.state = {}
                self.save()
                return feedback
            return None
        if feedback.job_status != "running":
            self.state = {}
            self.save()
            return None
        ref = claimed.lease.ref
        # Heartbeat runs independently of inference/data reads. Errors are resolved at the authoritative submit.
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(service["heartbeat_ms"] / 1000):
                try:
                    self.client.call(route + "/heartbeat", HeartbeatResponse, HeartbeatRequest(ref))
                except (RemoteError, OSError, ValueError):
                    pass

        self.client.call(route + "/heartbeat", HeartbeatResponse, HeartbeatRequest(ref))
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            if "submission" not in self.state:
                batch = self.produce(claimed, route)
                self.state["submission"] = ForecastSubmission("submit:" + self.state["claim_id"], ref, batch).to_dict()
                self.save()
            self.client.call(route + "/forecast", SubmissionResponse,
                             ForecastSubmission.from_dict(self.state["submission"]))
        finally:
            stop.set()
            thread.join(timeout=self.client.timeout + 1)
        return None

    def produce(self, claimed, route):
        simulation = self.config["simulation"]
        ref = claimed.lease.ref
        query_id = "fixture:" + self.state["claim_id"]
        result = self.client.call(route + "/data/query", DataQueryResponse,
                                  DataQueryRequest(query_id, ref, request_for(claimed.job.symbols,
                                                                             simulation["max_age_ms"])))
        if result.status != "ready" or result.asof_ms != claimed.job.asof_ms or result.truncated:
            raise ValueError("Invalid fixture response")
        rows = {record.symbol: record for record in result.records}
        items = tuple(Forecast(symbol, claimed.job.asof_ms,
                               claimed.job.asof_ms + simulation["forecast_ttl_ms"], simulation["horizon_ms"],
                               "ready", "up" if Decimal(rows[symbol].data["mark"]) > Decimal(rows[symbol].data["oracle"])
                               else "down", (result.evidence_ref,), reason="SYNTHETIC Dummy only")
                      for symbol in claimed.job.symbols)
        batch = ForecastBatch("dummy:" + self.state["claim_id"], claimed.job.context_id,
                              claimed.job.producer_id, claimed.job.producer_version,
                              max(int(time.time() * 1000), claimed.lease.claimed_ms), items)
        return batch

    def validate_claim(self, claimed):
        pass

    def run(self, once=False):
        delay = self.config["service"]["poll_ms"] / 1000
        while True:
            try:
                feedback = self.cycle()
                if feedback:
                    print(canonical({"mode": self.mode, "simulated": True,
                                     "feedback": feedback.to_dict()}), flush=True)
                    if once:
                        return
                delay = self.config["service"]["poll_ms"] / 1000
            except RemoteError as error:
                if error.code in {"LEASE_LOST", "EXPIRED", "STALE_RESULT"}:
                    # Query feedback on the next iteration before abandoning any accepted result.
                    claimed = ClaimResponse.from_dict(self.state["claim"])
                    try:
                        feedback = self.client.call("/v1/jobs/" + quote(claimed.job.job_id, safe="") + "/feedback",
                                                    FeedbackResponse)
                    except (OSError, urllib.error.URLError):
                        pass
                    else:
                        if feedback.receipt is None:
                            self.state = {}
                            self.save()
                elif not error.retryable:
                    raise
                delay = min(5, max(.1, delay * 2))
            except (OSError, urllib.error.URLError):
                delay = min(5, max(.1, delay * 2))
            time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description="HTTP-only synthetic Dummy worker")
    parser.add_argument("--config", default="config/mock-http.toml")
    parser.add_argument("--core-url", default="http://127.0.0.1:8080")
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--history", action="store_true", help="Two historical query rounds; shadow Jobs only")
    args = parser.parse_args()
    config = load_config(args.config)
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    with ProcessLock(str(Path(args.state).resolve()) + ".lock"):
        client = HTTPClient(args.core_url, token, timeout=config["service"]["http_timeout_ms"] / 1000,
                            max_bytes=config["service"]["max_http_body"])
        worker_type = DummyWorker
        if args.history:
            from .history_worker import HistoricalDummyWorker
            worker_type = HistoricalDummyWorker
        worker_type(client, args.state, config).run(args.once)


if __name__ == "__main__":
    main()
