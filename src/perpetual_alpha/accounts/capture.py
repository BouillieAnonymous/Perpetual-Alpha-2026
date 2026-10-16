"""Bracket mutable account endpoints; preserve every failure, never substitute a cached/empty result."""
import time

from ..historical.contracts import PRICE_UNITS
from ..journal import canonical
from ..runtime.contracts import decode
from .client import wallet_address
from .normalize import normalize


class CaptureBytesExceeded(ValueError):
    pass


class AccountCapture:
    def __init__(self, client, *, clock=None, max_capture_ms=60000, max_source_lag_ms=90000):
        decode(int, max_capture_ms)
        if not 1000 <= max_capture_ms <= 120000:
            raise ValueError("Bounded account capture duration required")
        self.client, self.clock = client, clock or (lambda: time.time_ns() // 1000000)
        self.max_capture_ms = max_capture_ms
        decode(int, max_source_lag_ms)
        if not 1 <= max_source_lag_ms <= 300000:
            raise ValueError("Bounded source freshness required")
        self.max_source_lag_ms = max_source_lag_ms

    def capture(self, wallet, capture_id):
        wallet = wallet_address(wallet)
        decode(str, capture_id)
        started = self.clock()
        observations = {}
        used_bytes = 0

        def fetch(label, kind, *, coin=None):
            nonlocal used_bytes
            begin = self.clock()
            request = {"type": kind}
            if kind != "metaAndAssetCtxs":
                request["user"] = wallet
            if kind in {"clearinghouseState", "frontendOpenOrders", "metaAndAssetCtxs"}:
                request["dex"] = "par"
            if coin is not None:
                request["coin"] = coin
            if used_bytes >= 8 * 1024 * 1024:
                row = {"request": request, "received_ms": begin, "payload": None, "error": "CAPTURE_BYTES"}
            elif begin < started or begin - started >= self.max_capture_ms:
                row = {"request": request, "received_ms": begin, "payload": None, "error": "CAPTURE_DEADLINE"}
            else:
                try:
                    row = self.client.read(kind, wallet, coin=coin)
                    if row["request"] != request:
                        raise ValueError("Client returned a different request identity")
                    size = len(canonical(row).encode())
                    if used_bytes + size > 8 * 1024 * 1024:
                        used_bytes = 8 * 1024 * 1024
                        raise CaptureBytesExceeded("Capture byte budget reached")
                    used_bytes += size
                except Exception as error:
                    row = {"request": request, "received_ms": self.clock(), "payload": None,
                           "error": type(error).__name__}
            observations[label] = {**row, "started_ms": begin}
            return row

        role = fetch("role_before", "userRole")
        if isinstance(role["payload"], dict) and role["payload"].get("role") == "user":
            fetch("mode_before", "userAbstraction")
            for stage in ("before", "after"):
                for label, kind in (("spot", "spotClearinghouseState"), ("perp", "clearinghouseState"),
                                    ("orders", "frontendOpenOrders")):
                    fetch(label + "_" + stage, kind)
                if stage == "before":
                    fetch("market", "metaAndAssetCtxs")
                    for symbol in PRICE_UNITS:
                        fetch("active/" + symbol, "activeAssetData", coin=symbol)
            fetch("mode_after", "userAbstraction")
            fetch("role_after", "userRole")
        ended = self.clock()
        bundle = {"capture_id": capture_id, "wallet": wallet, "source": "hyperliquid-testnet/par",
                  "started_ms": started, "ended_ms": ended, "max_capture_ms": self.max_capture_ms,
                  "max_source_lag_ms": self.max_source_lag_ms,
                  "observations": observations}
        return bundle, normalize(bundle)
