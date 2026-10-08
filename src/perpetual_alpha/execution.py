"""Explicitly enabled, testnet-only signing and reconciled order operations."""
import json
import os
import time
import uuid
from pathlib import Path

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils.types import Cloid

from .api_client import ReadOnlyClient, TESTNET_URL, account_ready, load_public_settings


class ExecutionClient:
    def __init__(self, *, enabled=False, env_path=".env", journal="data/order-test.jsonl"):
        if not enabled:
            raise ValueError("Trading requires explicit enabled=True")
        settings = load_public_settings(env_path)
        values = {}
        for line in Path(env_path).read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "API_WALLET_PRIVATE_KEY":
                values[key.strip()] = value.strip().strip("\"'")
        key = os.environ.get("API_WALLET_PRIVATE_KEY", values.get("API_WALLET_PRIVATE_KEY", ""))
        try:
            signer = Account.from_key(key)
        except Exception:
            raise ValueError("Invalid or missing local API Wallet key") from None
        if signer.address.lower() != settings["API_WALLET_ADDRESS"].lower():
            raise ValueError("API signing address mismatch")
        if signer.address.lower() == settings["TEAM_WALLET_ADDRESS"].lower():
            raise ValueError("Team Wallet signing is forbidden")
        self.read = ReadOnlyClient(settings["TEAM_WALLET_ADDRESS"])
        if not self.read.authorization(signer.address)["authorized"]:
            raise ValueError("API Wallet authorization missing or expired")
        if not account_ready(self.read.account()):
            raise ValueError("Account mode or collateral not ready")
        self.exchange = Exchange(signer, TESTNET_URL,
                                 account_address=self.read.team_wallet,
                                 perp_dexs=["par"], timeout=15)
        self.journal = Path(journal)
        self.journal.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event, **data):
        row = {"time_ms": int(time.time() * 1000), "event": event, **data}
        with self.journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps(row))

    def order_status(self, cloid):
        return self.read.info.query_order_by_cloid(self.read.team_wallet, cloid)

    def order(self, symbol, buy, quantity, price, tif, *, reduce_only=False):
        if symbol not in {"par:BTC", "par:ETH"}:
            raise ValueError("Only configured competition markets allowed")
        if not (quantity > 0 and price > 0 and quantity * price <= 25):
            raise ValueError("Test order must be positive and at most 25 USDPR")
        cloid = Cloid.from_str("0x" + uuid.uuid4().hex)
        self.record("intent", cloid=cloid.to_raw(), symbol=symbol, buy=buy,
                    quantity=quantity, price=price, tif=tif, reduce_only=reduce_only)
        try:
            response = self.exchange.order(symbol, buy, quantity, price,
                                           {"limit": {"tif": tif}}, reduce_only=reduce_only, cloid=cloid)
        except Exception:
            self.record("network_unknown", cloid=cloid.to_raw())
            # Never resubmit an order whose outcome is unknown.
            try:
                self.record("reconciliation", result=self.order_status(cloid))
            except Exception:
                self.record("reconciliation_unavailable")
            raise RuntimeError("Order outcome uncertain; inspect journal and account before continuing") from None
        self.record("order_response", cloid=cloid.to_raw(), result=response)
        if response.get("status") != "ok":
            raise RuntimeError("Exchange rejected action")
        statuses = response.get("response", {}).get("data", {}).get("statuses", [])
        if not statuses or any(isinstance(s, dict) and "error" in s for s in statuses):
            raise RuntimeError("Exchange rejected order; see sanitized journal")
        confirmed = self.order_status(cloid)
        self.record("order_status", cloid=cloid.to_raw(), result=confirmed)
        allowed = {"filled", "canceled"} if tif == "Ioc" else {"open", "filled"}
        if confirmed.get("status") != "order" or confirmed["order"]["status"] not in allowed:
            raise RuntimeError("Order state not confirmed; no further orders allowed")
        return cloid

    def cancel(self, symbol, cloid):
        if symbol not in {"par:BTC", "par:ETH"}:
            raise ValueError("Unsupported market")
        result = self.exchange.cancel_by_cloid(symbol, cloid)
        self.record("cancel_response", result=result)
        status = self.order_status(cloid)
        self.record("cancel_status", result=status)
        if status.get("status") != "order" or status["order"]["status"] not in {"canceled", "filled"}:
            raise RuntimeError("Cancellation not confirmed")

    def quantity(self, symbol):
        state = self.read.account()
        quantity = sum(float(p["position"]["szi"]) for p in state["positions"]
                       if p["position"]["coin"] == symbol)
        return quantity, state

    def close(self):
        self.read.close()
        self.exchange.session.close()
        self.exchange.info.session.close()
