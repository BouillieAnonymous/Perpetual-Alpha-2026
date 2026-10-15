"""Bounded quote requests from the existing store, frozen by event AND receipt time."""
import copy
import json
import sqlite3
from decimal import Decimal

from .contracts import symbol
from .interfaces import PROTOCOL_VERSION, decimal_string, identity, timestamp

SOURCE = "hyperliquid-testnet/par"
QUOTE_FIELDS = {"bid", "ask", "mark", "oracle", "funding"}


def validate_account(account, asof_ms, max_age_ms):
    if not isinstance(account, dict) or set(account) != {
        "observed_ms", "status", "equity_usd", "available_margin_usd", "positions", "open_orders"
    }:
        raise ValueError("Missing or unexpected account fields")
    timestamp(account["observed_ms"])
    if account["status"] != "ready" or not 0 <= asof_ms - account["observed_ms"] <= max_age_ms:
        raise ValueError("Account unavailable, stale or future")
    for field in ("equity_usd", "available_margin_usd"):
        if account[field] is None or decimal_string(account[field]) < 0:
            raise ValueError("Account amounts unknown or invalid")
    if not isinstance(account["positions"], list) or not isinstance(account["open_orders"], list):
        raise ValueError("Account positions/orders must be arrays")
    seen = set()
    for position in account["positions"]:
        if set(position) != {"symbol", "quantity", "mark_price"}:
            raise ValueError("Invalid position fields")
        symbol(position["symbol"])
        if position["symbol"] in seen:
            raise ValueError("Duplicate account position")
        seen.add(position["symbol"])
        decimal_string(position["quantity"])
        if decimal_string(position["mark_price"]) <= 0:
            raise ValueError("Invalid position mark price")
    order_ids = set()
    for order in account["open_orders"]:
        if set(order) != {"order_id", "symbol", "side", "remaining_quantity", "reduce_only"}:
            raise ValueError("Invalid open order fields")
        identity(order["order_id"])
        symbol(order["symbol"])
        if order["order_id"] in order_ids or order["side"] not in {"buy", "sell"}:
            raise ValueError("Invalid or duplicate open order")
        order_ids.add(order["order_id"])
        if decimal_string(order["remaining_quantity"]) < 0 or type(order["reduce_only"]) is not bool:
            raise ValueError("Invalid remaining quantity/reduce_only")
    return account


class ContextBuilder:
    def __init__(self, connection):
        self.connection = connection

    def build(self, requests, context_id, asof_ms, account=None, execution_reports=()):
        identity(context_id)
        timestamp(asof_ms)
        if len({r.dataset_id for r in requests}) != len(requests):
            raise ValueError("Duplicate dataset id")
        datasets = {r.dataset_id: self._dataset(r, asof_ms) for r in requests}
        # Reports and account from after the decision time are never exposed.
        if account is not None:
            timestamp(account["observed_ms"])
            if account["observed_ms"] > asof_ms:
                account = None
        reports = []
        for report in execution_reports:
            timestamp(report["observed_ms"])
            if report["observed_ms"] <= asof_ms:
                reports.append(copy.deepcopy(report))
        return {"protocol_version": PROTOCOL_VERSION, "context_id": context_id, "asof_ms": asof_ms,
                "datasets": datasets, "account": copy.deepcopy(account), "execution_reports": reports}

    def _dataset(self, request, asof_ms):
        dataset = {"schema_version": "quote/1.0", "source": request.source, "kind": request.kind,
                   "parameters": copy.deepcopy(request.parameters), "status": "missing",
                   "required": request.required, "truncated": False, "error": None,
                   "units": {f: "simple-rate-per-hour" if f == "funding" else "USDPR/base-asset"
                             for f in request.fields}, "records": []}
        try:
            if request.source != SOURCE or request.kind != "quote" or request.parameters:
                raise ValueError("UNSUPPORTED: only stored par quotes with empty parameters are supported")
            if not request.fields or not set(request.fields) <= QUOTE_FIELDS:
                raise ValueError("UNSUPPORTED: unknown or empty quote fields")
            if not request.symbols or len(set(request.symbols)) != len(request.symbols):
                raise ValueError("UNSUPPORTED: nonempty unique symbols required")
            for s in request.symbols:
                symbol(s)
                if request.lookback_ms:
                    anchor = self.connection.execute(
                        "SELECT 1 FROM snapshots WHERE symbol=? AND event_ms<=? AND received_ms<=? LIMIT 1",
                        (s, asof_ms - request.lookback_ms, asof_ms),
                    ).fetchone()
                    if anchor is None:
                        dataset["error"] = "INSUFFICIENT_HISTORY"
                        return dataset
                records = self.connection.execute(
                    "SELECT raw_json FROM snapshots WHERE symbol=? AND event_ms<=? AND received_ms<=? "
                    "AND event_ms>=? ORDER BY event_ms DESC LIMIT ?",
                    (s, asof_ms, asof_ms, max(0, asof_ms - request.lookback_ms) if request.lookback_ms else 0,
                     request.max_records + 1 if request.lookback_ms else 1),
                ).fetchall()
                if not records:
                    dataset["error"] = "MISSING_SYMBOL:" + s
                    dataset["records"] = []
                    return dataset
                if len(records) > request.max_records:
                    dataset["truncated"] = True
                    records = records[:request.max_records]
                for raw, in records:
                    row = json.loads(raw)
                    if row.get("source") != SOURCE or row.get("symbol") != s or row.get("schema_version") != 1:
                        raise ValueError("Invalid stored quote identity/schema")
                    timestamp(row["event_ms"])
                    timestamp(row["received_ms"])
                    if not row["event_ms"] <= row["received_ms"] <= asof_ms:
                        raise ValueError("Invalid stored quote time")
                    if row.get("context_received_ms") is not None:
                        timestamp(row["context_received_ms"])
                        if row["context_received_ms"] > row["received_ms"]:
                            raise ValueError("Future stored context")
                    values = {f: str(row[f]) for f in request.fields}
                    for f, value in values.items():
                        if f != "funding" and decimal_string(value) <= 0:
                            raise ValueError("Nonpositive price")
                        decimal_string(value)
                    if "bid" in values and "ask" in values:
                        if Decimal(values["bid"]) > Decimal(values["ask"]):
                            raise ValueError("Crossed quote")
                    dataset["records"].append({"record_id": f"{s}:{row['event_ms']}", "symbol": s,
                                               "event_ms": row["event_ms"], "received_ms": row["received_ms"],
                                               "data": values})
                newest = json.loads(records[0][0])
                ages = [asof_ms - newest["received_ms"], asof_ms - newest["event_ms"]]
                if set(request.fields) & {"mark", "oracle", "funding"}:
                    ages.append(asof_ms - (newest.get("context_received_ms") or newest["received_ms"]))
                if any(age > request.max_age_ms for age in ages):
                    dataset["status"] = "stale"
                    dataset["error"] = "STALE_QUOTE"
            dataset["records"].sort(key=lambda r: (r["event_ms"], r["symbol"]))
            # max_records is per dataset, not per asset. Do not hide a missing asset under ready.
            if len(dataset["records"]) > request.max_records:
                dataset["records"] = dataset["records"][-request.max_records:]
                dataset["truncated"] = True
            if set(request.symbols) != {r["symbol"] for r in dataset["records"]}:
                dataset["status"] = "missing"
                dataset["error"] = "RECORD_LIMIT_OMITS_SYMBOL"
            elif dataset["status"] != "stale":
                dataset["status"] = "ready"
        except (ValueError, TypeError, KeyError, ArithmeticError, sqlite3.Error) as error:
            dataset.update(status="error", error=str(error), records=[])
        return dataset
