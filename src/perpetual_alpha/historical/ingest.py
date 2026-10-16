"""Finite paged ingestion; network calls finish before short history write transactions."""
from uuid import uuid4

from .contracts import window


class HistoryIngestor:
    def __init__(self, store, client):
        self.store, self.client = store, client

    def metadata(self):
        payload, received = self.client.metadata()
        return self.store.record_metadata(str(uuid4()), payload, received)

    def backfill(self, symbol, interval, start_ms, end_ms, *, max_requests=20, stopped=None):
        step = window(symbol, interval, start_ms, end_ms)
        if type(max_requests) is not int or not 1 <= max_requests <= 20:
            raise ValueError("Request budget must be 1..20")
        pages = ((end_ms - start_ms) // step + 499) // 500
        if pages > max_requests:
            raise ValueError("Requested window exceeds network request budget")
        summaries = []
        for start in range(start_ms, end_ms, 500 * step):
            if stopped is not None and stopped():
                break
            end = min(end_ms, start + 500 * step)
            payload, received = self.client.candles(symbol, interval, start, end)
            # No cursor can advance on failure; committed earlier pages remain reproducible.
            summaries.append(self.store.record_candles(str(uuid4()), symbol, interval, start, end, payload, received))
        return {"source": "hyperliquid-testnet/par", "requests": len(summaries), "pages": summaries,
                "interrupted": len(summaries) < pages,
                "coverage": self.store.read_candles(symbol, interval, start_ms, end_ms, self.store.clock())}
