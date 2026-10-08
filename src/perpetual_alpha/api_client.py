"""Official SDK public queries, restricted to competition testnet/par."""
import os
import re
import time
from decimal import Decimal
from pathlib import Path

from hyperliquid.info import Info

TESTNET_URL = "https://api.hyperliquid-testnet.xyz"


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ValueError("A valid public wallet address is required")
    return value.lower()


def load_public_settings(path=".env"):
    """Only public settings are selected; signing keys are never loaded."""
    settings = {}
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in {"TEAM_WALLET_ADDRESS", "API_WALLET_ADDRESS"}:
                settings[key.strip()] = value.strip().strip("\"'")
    for key in ("TEAM_WALLET_ADDRESS", "API_WALLET_ADDRESS"):
        settings[key] = os.environ.get(key, settings.get(key, ""))
    return settings


class ReadOnlyClient:
    def __init__(self, team_wallet=None):
        self.team_wallet = address(team_wallet) if team_wallet else None
        self.info = Info(TESTNET_URL, skip_ws=True, perp_dexs=["par"], timeout=15)

    def close(self):
        self.info.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def team(self):
        if not self.team_wallet:
            raise ValueError("Set public TEAM_WALLET_ADDRESS first")
        role = self.info.post("/info", {"type": "userRole", "user": self.team_wallet})
        if role.get("role") == "agent":
            raise ValueError("TEAM_WALLET_ADDRESS is an API agent; use its owner address")
        return self.team_wallet

    def metadata(self):
        return self.info.meta(dex="par")

    def market(self, symbol):
        if not isinstance(symbol, str) or not symbol.startswith("par:"):
            raise ValueError("Only par: markets are allowed")
        meta, contexts = self.info.post("/info", {"type": "metaAndAssetCtxs", "dex": "par"})
        if len(meta["universe"]) != len(contexts):
            raise ValueError("Invalid market context response")
        lookup = dict(zip((item["name"] for item in meta["universe"]), contexts))
        if symbol not in lookup:
            raise ValueError("Unknown par market")
        book = self.info.l2_snapshot(symbol)
        if book.get("coin") != symbol or not all(book["levels"]):
            raise ValueError("Wrong or incomplete order book")
        age_ms = int(time.time() * 1000) - book["time"]
        if not 0 <= age_ms <= 90000:
            raise ValueError("Stale or future order book")
        return {"symbol": symbol, "context": lookup[symbol], "book": book}

    def account(self):
        team = self.team()
        spot = self.info.spot_user_state(team)
        balance = next((item for item in spot["balances"] if item["coin"] == "USDPR"), None)
        state = self.info.user_state(team, dex="par")
        orders = self.info.open_orders(team, dex="par")
        fills = self.info.user_fills(team)
        return {"team_wallet": team,
                "account_mode": self.info.query_user_abstraction_state(team),
                "usdpr_total": balance["total"] if balance else None,
                "usdpr_hold": balance["hold"] if balance else None,
                "position_count": len(state["assetPositions"]),
                "positions": state["assetPositions"], "open_orders": orders,
                "recent_par_fills": [fill for fill in fills if fill["coin"].startswith("par:")],
                "margin_state": state}

    def authorization(self, api_wallet):
        team = self.team()
        api_wallet = address(api_wallet)
        agents = self.info.post("/info", {"type": "extraAgents", "user": team})
        agent = next((item for item in agents if item["address"].lower() == api_wallet), None)
        return {"team_wallet": team, "api_wallet": api_wallet, "authorization": agent,
                "authorized": bool(agent and agent["validUntil"] > int(time.time() * 1000))}


def account_ready(result):
    return result["account_mode"] == "unifiedAccount" and result["usdpr_total"] is not None and Decimal(result["usdpr_total"]) > 0
