"""Fixed testnet /info queries. No SDK Exchange, signing, .env or address discovery."""
import re

from ..historical.client import PublicHistoryClient
from ..historical.contracts import PRICE_UNITS


def wallet_address(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ValueError("Explicit public account address required")
    return value.lower()


class AccountInfoClient:
    def __init__(self, *, timeout=10, clock=None):
        self.transport = PublicHistoryClient(timeout=timeout, clock=clock)

    def read(self, kind, wallet=None, *, coin=None):
        if kind not in {"userRole", "userAbstraction", "spotClearinghouseState", "clearinghouseState",
                        "frontendOpenOrders", "metaAndAssetCtxs", "activeAssetData"}:
            raise ValueError("Unsupported read-only account query")
        payload = {"type": kind}
        if kind != "metaAndAssetCtxs":
            payload["user"] = wallet_address(wallet)
        if kind in {"clearinghouseState", "frontendOpenOrders", "metaAndAssetCtxs"}:
            payload["dex"] = "par"
        if kind == "activeAssetData":
            if coin not in PRICE_UNITS:
                raise ValueError("Only competition active asset queries allowed")
            payload["coin"] = coin
        elif coin is not None:
            raise ValueError("Unexpected coin parameter")
        response, received = self.transport._request(payload)
        return {"request": payload, "received_ms": received, "payload": response, "error": None}
