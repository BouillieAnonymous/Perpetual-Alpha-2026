"""Bounded public /info requests only. No wallet, .env, SDK Exchange or arbitrary URL."""
import json
import time
import urllib.request

from .contracts import window

ENDPOINT = "https://api.hyperliquid-testnet.xyz/info"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class PublicHistoryClient:
    def __init__(self, *, timeout=15, min_spacing_seconds=1, clock=None):
        if not 0 < timeout <= 60 or not 1 <= min_spacing_seconds <= 60:
            raise ValueError("Bounded timeout and conservative public request spacing required")
        self.timeout, self.spacing = timeout, min_spacing_seconds
        self.clock, self.last_request = clock or (lambda: time.time_ns() // 1000000), None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _request(self, payload):
        # This synchronous client is owned by one collector; retries are explicit at the caller.
        if self.last_request is not None:
            wait = self.spacing - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
        self.last_request = time.monotonic()
        request = urllib.request.Request(ENDPOINT, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        received = self.clock()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Public history response too large")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("Duplicate source JSON key")
                result[key] = value
            return result

        def invalid(value):
            raise ValueError("Nonfinite source JSON")

        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid), received

    def metadata(self):
        return self._request({"type": "meta", "dex": "par"})

    def candles(self, symbol, interval, start_ms, end_ms):
        window(symbol, interval, start_ms, end_ms, maximum=500)
        return self._request({"type": "candleSnapshot", "req": {"coin": symbol, "interval": interval,
                                                               "startTime": start_ms, "endTime": end_ms - 1}})
