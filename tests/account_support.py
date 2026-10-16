"""Entirely synthetic account provider; no wallet ownership claim and no network."""
import copy

from perpetual_alpha.historical.contracts import PRICE_UNITS

WALLET = "0x" + "1" * 40


class Clock:
    def __init__(self):
        self.now = 100000

    def __call__(self):
        return self.now


def position(symbol="par:BTC", quantity="2"):
    return {"type": "oneWay", "position": {"coin": symbol, "szi": quantity, "entryPx": "100",
            "positionValue": "200", "unrealizedPnl": "3", "marginUsed": "10",
            "leverage": {"type": "cross", "value": 5}, "liquidationPx": None}}


def order(oid=1, symbol="par:ETH", side="B", quantity="3", **kwargs):
    return {"coin": symbol, "oid": oid, "side": side, "sz": quantity, "origSz": quantity,
            "limitPx": "100", "reduceOnly": False, "isTrigger": False, "isPositionTpsl": False,
            "timestamp": 90000, "orderType": "Limit", "triggerPx": "0", **kwargs}


class FakeInfoClient:
    def __init__(self, clock):
        self.clock, self.calls, self.failures, self.transforms = clock, [], set(), {}
        summary = {"accountValue": "1", "totalMarginUsed": "10", "totalNtlPos": "200", "totalRawUsd": "-2"}
        self.payloads = {
            "userRole": {"role": "user"}, "userAbstraction": "unifiedAccount",
            "spotClearinghouseState": {"balances": [{"coin": "USDPR", "token": 3032, "total": "1000", "hold": "100", "entryNtl": "0"}],
                                        "tokenToAvailableAfterMaintenance": [[3032, "700"]]},
            "clearinghouseState": {"assetPositions": [position(), position("par:HYPE", "-1")], "time": 99999,
                                    "marginSummary": summary, "crossMarginSummary": summary,
                                    "crossMaintenanceMarginUsed": "5", "withdrawable": "42"},
            "frontendOpenOrders": [order(), order(2, "par:BTC", "A", "1", reduceOnly=True, isTrigger=True)],
            "metaAndAssetCtxs": [{"universe": [{"name": s, "szDecimals": 4, "maxLeverage": 25} for s in PRICE_UNITS],
                                  "collateralToken": 3032}, [{"markPx": "101"} for _ in PRICE_UNITS]],
        }

    def read(self, kind, wallet, *, coin=None):
        self.calls.append((kind, coin))
        count = sum(1 for k, _ in self.calls if k == kind)
        if (kind, count) in self.failures:
            raise TimeoutError("synthetic failure")
        payload = {"user": wallet, "coin": coin, "availableToTrade": ["123", "124"], "maxTradeSzs": ["1", "2"],
                   "markPx": "101", "leverage": {"type": "cross", "value": 5}} if kind == "activeAssetData" else copy.deepcopy(self.payloads[kind])
        if (kind, count) in self.transforms:
            payload = self.transforms[kind, count](payload)
        request = {"type": kind}
        if kind != "metaAndAssetCtxs":
            request["user"] = wallet
        if kind in {"clearinghouseState", "frontendOpenOrders", "metaAndAssetCtxs"}:
            request["dex"] = "par"
        if coin is not None:
            request["coin"] = coin
        self.clock.now += 1
        return {"request": request, "received_ms": self.clock(), "payload": payload, "error": None}
