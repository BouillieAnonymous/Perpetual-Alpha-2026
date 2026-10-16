"""Predeclared synthetic inputs; deliberately separate from the market data collector."""
import copy
from decimal import Decimal

from ..interfaces import DataRequest


def request_for(symbols, max_age_ms):
    return DataRequest("fixture-quotes", "fixture/par", "quote", tuple(symbols),
                       ("bid", "ask", "mark", "oracle"), 0, len(symbols), max_age_ms)


def initial_account(config, now):
    return {"observed_ms": now, "status": "ready", "equity_usd": config["equity_usd"],
            "available_margin_usd": config["available_margin_usd"], "positions": [], "open_orders": []}


def context_for(config, context_id, asof, account):
    request = request_for(tuple(config["fixture_prices"]), config["max_age_ms"])
    records = []
    for symbol, price in config["fixture_prices"].items():
        records.append({"record_id": context_id + ":" + symbol, "symbol": symbol,
                        "event_ms": max(0, asof - 1000), "received_ms": max(0, asof - 500),
                        "data": {"bid": str(Decimal(price) - 1), "ask": str(Decimal(price) + 1),
                                 "mark": price, "oracle": str(Decimal(price) - 2)}})
    synthetic = copy.deepcopy(account)
    # This is a scenario fixture at the frozen cutoff, never a claim about an external account.
    synthetic["observed_ms"] = asof
    return {"protocol_version": "1.0", "context_id": context_id, "asof_ms": asof,
            "account": synthetic, "execution_reports": [], "datasets": {request.dataset_id: {
                "source": request.source, "kind": request.kind, "parameters": {}, "required": True,
                "status": "ready", "records": records, "units": {f: "USD" for f in request.fields},
                "truncated": False, "error": None}}}, (request,)
