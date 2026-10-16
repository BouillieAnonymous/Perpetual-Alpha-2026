"""Multi-round HTTP Dummy, always shadow. It has no history DB handle or signing capabilities."""
import time
from decimal import Decimal

from ..contracts import Forecast, ForecastBatch
from ..interfaces import DataRequest
from ..journal import digest
from .data_gateway import response_digest
from .dummy_worker import DummyWorker
from .messages import DataQueryRequest, DataQueryResponse


class HistoricalDummyWorker(DummyWorker):
    mode = "history-shadow"

    def validate_claim(self, claimed):
        if claimed.job.role != "shadow":
            raise ValueError("Historical Dummy only accepts shadow Jobs")

    def produce(self, claimed, route):
        self.validate_claim(claimed)
        end = claimed.job.asof_ms // 60000 * 60000 - 180000
        if end < 600000:
            raise ValueError("Demo requires a contemporary job timestamp")
        results = {}
        # Round two changes the time window; every round is persisted/deduplicated by the Core.
        for round_no, bars in enumerate((5, 10)):
            dataset_id = "history:" + digest({"job": claimed.job.job_id, "round": round_no})
            request = DataRequest(dataset_id, "hyperliquid-testnet/par", "candle", claimed.job.symbols,
                                  ("close",), bars * 60000, 100, 600000, False,
                                  {"interval": "1m", "end_ms": end})
            response = self.client.call(route + "/data/query", DataQueryResponse,
                                        DataQueryRequest("history:" + str(round_no), claimed.lease.ref, request))
            if response.asof_ms != claimed.job.asof_ms or response_digest(response) != response.payload_digest:
                raise ValueError("Historical response identity/digest mismatch")
            if response.status == "ready" and not response.truncated and response.evidence_ref is not None:
                results[dataset_id] = response
        items = []
        for symbol in claimed.job.symbols:
            ready = next(((dataset_id, [r for r in response.records if r.symbol == symbol])
                          for dataset_id, response in reversed(tuple(results.items()))
                          if any(r.symbol == symbol for r in response.records)), None)
            direction = None
            if ready:
                first, last = Decimal(ready[1][0].data["close"]), Decimal(ready[1][-1].data["close"])
                direction = "up" if last > first else "down" if last < first else "neutral"
            items.append(Forecast(symbol, claimed.job.asof_ms,
                                  claimed.job.asof_ms + self.config["simulation"]["forecast_ttl_ms"],
                                  self.config["simulation"]["horizon_ms"], "ready" if ready else "abstain",
                                  direction, (ready[0],) if ready else (), reason="Dummy shadow; no trading"))
        return ForecastBatch("dummy:" + self.state["claim_id"], claimed.job.context_id, claimed.job.producer_id,
                             claimed.job.producer_version, max(int(time.time() * 1000), claimed.lease.claimed_ms), tuple(items))
