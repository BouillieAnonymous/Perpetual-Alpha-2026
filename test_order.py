"""Manual small test: resting order/cancel, IOC entry, reduce-only exit."""
import argparse
import os
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from perpetual_alpha.execution import ExecutionClient
from perpetual_alpha.models import limit_price


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Explicitly authorize testnet orders")
    args = parser.parse_args()
    if not args.execute:
        print("TRADING_DISABLED: use --execute only for an authorized small testnet test")
        return
    Path("data").mkdir(exist_ok=True)
    lock = Path("data/order-test.lock")
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    client = None
    succeeded = False
    try:
        client = ExecutionClient(enabled=True)
        symbol = "par:BTC"
        _, account = client.quantity(symbol)
        if account["positions"] or account["open_orders"]:
            raise RuntimeError("Test requires no existing par positions or orders")
        meta = next(m for m in client.read.metadata()["universe"] if m["name"] == symbol)
        decimals = meta["szDecimals"]
        leverage = client.exchange.update_leverage(1, symbol, is_cross=True)
        client.record("leverage_response", result=leverage)
        if leverage.get("status") != "ok":
            raise RuntimeError("Leverage setup rejected")
        book = client.read.market(symbol)["book"]
        bid = float(book["levels"][0][0]["px"])
        resting_price = limit_price(bid * .99, decimals, True)
        lot = Decimal(10) ** -decimals
        quantity = float((Decimal("11") / Decimal(str(resting_price)) / lot)
                         .to_integral_value(rounding=ROUND_CEILING) * lot)
        cloid = client.order(symbol, True, quantity, resting_price, "Alo")
        client.cancel(symbol, cloid)
        actual, account = client.quantity(symbol)
        if account["open_orders"]:
            raise RuntimeError("Unexpected remaining orders")
        if actual == 0:
            book = client.read.market(symbol)["book"]
            ask = float(book["levels"][1][0]["px"])
            client.order(symbol, True, quantity, limit_price(ask * 1.0005, decimals, True), "Ioc")
        for _ in range(3):
            actual, _ = client.quantity(symbol)
            if actual == 0:
                break
            book = client.read.market(symbol)["book"]
            buy = actual < 0
            px = float(book["levels"][1 if buy else 0][0]["px"])
            client.order(symbol, buy, abs(actual),
                         limit_price(px * (1.0005 if buy else .9995), decimals, buy),
                         "Ioc", reduce_only=True)
        actual, account = client.quantity(symbol)
        client.record("final_account", quantity=actual, positions=account["positions"],
                      open_orders=account["open_orders"], fills=account["recent_par_fills"],
                      usdpr_total=account["usdpr_total"])
        if account["positions"] or account["open_orders"]:
            raise RuntimeError("Remaining exposure; manual inspection required")
        if not account["recent_par_fills"]:
            raise RuntimeError("No actual fills; execution path not validated")
        succeeded = True
        print("TEST_ORDER_PASSED: no remaining par positions or orders")
    except Exception as exc:
        print("TEST_STOPPED:", str(exc) if isinstance(exc, (RuntimeError, ValueError, FileExistsError))
              else type(exc).__name__)
        if client:
            try:
                actual, state = client.quantity("par:BTC")
                client.record("stopped_account", quantity=actual, positions=state["positions"],
                              open_orders=state["open_orders"])
            except Exception:
                print("Account state unavailable; inspect testnet account manually")
        raise SystemExit(1) from None
    finally:
        if client:
            client.close()
        if succeeded:
            lock.unlink()


if __name__ == "__main__":
    run()
