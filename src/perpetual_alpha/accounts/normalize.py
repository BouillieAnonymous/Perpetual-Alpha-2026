"""Explicitly scoped account evidence. Unknown is nullable; no quantity×price valuation guesses."""
import copy
from decimal import Decimal

from ..historical.contracts import PRICE_UNITS
from ..interfaces import decimal_string
from ..journal import canonical, digest
from ..runtime.contracts import decode
from .client import wallet_address

SCHEMA = "account-observation/0.1"


def amount(value, *, nonnegative=False):
    if not isinstance(value, str) or len(value) > 128 or value.strip() != value:
        raise ValueError("Bounded decimal string required")
    result = decimal_string(value)
    if abs(result.adjusted()) > 100 or (nonnegative and result < 0):
        raise ValueError("Invalid amount")
    return value


def sequence(value, maximum=1000):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("Invalid/bounded account list required")
    return value


def leverage(value):
    if not isinstance(value, dict) or value.get("type") not in {"cross", "isolated"}:
        raise ValueError("Unknown leverage mode")
    decode(int, value["value"])
    if not 1 <= value["value"] <= 1000:
        raise ValueError("Invalid reported leverage")
    if "rawUsd" in value:
        amount(value["rawUsd"])
    return value


def normalize(bundle):
    bundle = copy.deepcopy(bundle)
    wallet = wallet_address(bundle["wallet"])
    for key in ("started_ms", "ended_ms", "max_capture_ms"):
        decode(int, bundle[key])
    decode(int, bundle["max_source_lag_ms"])
    if bundle["source"] != "hyperliquid-testnet/par" or bundle["ended_ms"] < bundle["started_ms"]:
        raise ValueError("Invalid account capture identity/clock")
    issues, observations = [], bundle["observations"]
    if len(observations) > 24:
        raise ValueError("Account capture query budget exceeded")
    components = {}

    def get(label):
        row = observations.get(label)
        if row is None:
            issues.append(label + ":MISSING")
            components[label] = {"status": "missing", "received_ms": None}
            return None
        decode(int, row["received_ms"])
        decode(int, row["started_ms"])
        expected_kind = ("activeAssetData" if label.startswith("active/") else
                         {"role": "userRole", "mode": "userAbstraction", "spot": "spotClearinghouseState",
                          "perp": "clearinghouseState", "orders": "frontendOpenOrders", "market": "metaAndAssetCtxs"}[label.split("_")[0]])
        expected_request = {"type": expected_kind}
        if label != "market":
            expected_request["user"] = wallet
        if expected_kind in {"clearinghouseState", "frontendOpenOrders", "metaAndAssetCtxs"}:
            expected_request["dex"] = "par"
        if label.startswith("active/"):
            expected_request["coin"] = label.removeprefix("active/")
        if row["request"] != expected_request:
            issues.append(label + ":IDENTITY_MISMATCH")
            components[label] = {"status": "invalid", "received_ms": row["received_ms"]}
            return None
        valid_time = bundle["started_ms"] <= row["started_ms"] <= row["received_ms"] <= bundle["ended_ms"]
        if not valid_time or row["error"] is not None:
            if row["error"] in {"CAPTURE_BYTES", "CaptureBytesExceeded"}:
                issues.append("CAPTURE_BYTE_BUDGET_EXCEEDED")
            issues.append(label + (":INVALID_TIME" if not valid_time else ":QUERY_FAILED"))
            components[label] = {"status": "error", "received_ms": row["received_ms"]}
            return None
        components[label] = {"status": "observed", "received_ms": row["received_ms"], "payload_digest": digest(row["payload"])}
        return row["payload"]

    def invalid(label):
        issues.append(label + ":INVALID")
        components[label] = {**components.get(label, {}), "status": "invalid"}

    role = get("role_before")
    is_user = isinstance(role, dict) and role.get("role") == "user"
    result = {"schema_version": SCHEMA, "source": bundle["source"], "wallet": wallet,
              "wallet_identity_source": "operator-supplied-public-address", "team_ownership_verified": False,
              "capture_id": bundle["capture_id"], "started_ms": bundle["started_ms"], "observed_ms": bundle["ended_ms"],
              "status": "unavailable", "atomic": False, "consistency": "unverified", "account_mode": None,
              "scope": "testnet/par positions+orders and spot token balances", "global_account_complete": False,
              "other_dex_exposure": "unknown", "inflight_order_exposure": "unknown-no-intent-ledger",
              "equity_usd": None, "available_margin_usd": None, "risk_ready": False,
              "balances": [], "positions": [], "open_orders": [], "buying_power": {},
              "venue_perp_summary": None, "collateral": None, "exposures": {}, "components": components}
    result.update(par_positions_complete=False, par_orders_complete=False, balances_complete=False)
    result["order_changes"] = {"appeared": [], "disappeared_unresolved": [], "changed": []}
    if not is_user:
        issues.append("ACCOUNT_ROLE_UNVERIFIED")
        result.update(issues=sorted(set(issues)), payload_digest="")
        result["payload_digest"] = digest({k: v for k, v in result.items() if k != "payload_digest"})
        return result
    mode_before, mode_after, role_after = get("mode_before"), get("mode_after"), get("role_after")
    result["account_mode"] = mode_after if isinstance(mode_after, str) else None
    if mode_before != "unifiedAccount" or mode_after != "unifiedAccount":
        issues.append("UNSUPPORTED_OR_UNKNOWN_ACCOUNT_MODE")
    if not isinstance(role_after, dict) or role_after.get("role") != "user":
        issues.append("ACCOUNT_ROLE_CHANGED")
    market = get("market")
    marks, collateral_token = {}, None
    try:
        if not isinstance(market, list) or len(market) != 2:
            raise ValueError("Invalid market response")
        metadata, contexts = market
        universe = sequence(metadata["universe"])
        if len(universe) != len(contexts):
            raise ValueError("Market context length mismatch")
        collateral_token = metadata["collateralToken"]
        decode(int, collateral_token)
        for item, context in zip(universe, contexts):
            name = item["name"]
            decode(str, name)
            if name in marks:
                raise ValueError("Duplicate market")
            marks[name] = amount(context["markPx"], nonnegative=True)
            if Decimal(marks[name]) <= 0:
                raise ValueError("Nonpositive mark")
        if not set(PRICE_UNITS) <= marks.keys():
            issues.append("MARKET_ASSETS_MISSING")
    except (ValueError, KeyError, TypeError, ArithmeticError):
        invalid("market")
        marks, collateral_token = {}, None
    spot_before, spot_after = get("spot_before"), get("spot_after")
    balances_complete = False
    try:
        tokens, names = set(), set()
        rows = sequence(spot_after["balances"])
        after_maintenance = {}
        for pair in sequence(spot_after.get("tokenToAvailableAfterMaintenance", [])):
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("Invalid maintenance balance")
            decode(int, pair[0])
            if pair[0] in after_maintenance:
                raise ValueError("Duplicate maintenance token")
            after_maintenance[pair[0]] = amount(pair[1])
        for row in rows:
            token, coin = row["token"], row["coin"]
            decode(int, token)
            decode(str, coin)
            if token in tokens or coin in names:
                raise ValueError("Duplicate collateral balance")
            tokens.add(token)
            names.add(coin)
            total, hold = amount(row["total"]), amount(row["hold"], nonnegative=True)
            result["balances"].append({"coin": coin, "token": token, "total": total, "hold": hold,
                                       "unheld_balance": str(Decimal(total) - Decimal(hold)),
                                       "available_after_maintenance": after_maintenance.get(token), "unit": coin})
        collateral = next((b for b in result["balances"] if b["coin"] == "USDPR"), None)
        if collateral is None or collateral["token"] != collateral_token:
            issues.append("COLLATERAL_IDENTITY_UNVERIFIED")
        else:
            result["collateral"] = collateral
        balances_complete = True
    except (ValueError, KeyError, TypeError, ArithmeticError):
        invalid("spot_after")
    perp_before, perp_after = get("perp_before"), get("perp_after")
    positions_complete = False
    try:
        decode(int, perp_after["time"])
        if perp_after["time"] > observations["perp_after"]["received_ms"]:
            raise ValueError("Future position state")
        components["perp_after"]["event_ms"] = perp_after["time"]
        stale_perp = observations["perp_after"]["received_ms"] - perp_after["time"] > bundle["max_source_lag_ms"]
        if stale_perp:
            issues.append("PERP_STATE_STALE")
        result["venue_perp_summary"] = {k: perp_after.get(k) for k in (
            "marginSummary", "crossMarginSummary", "crossMaintenanceMarginUsed", "withdrawable", "time")}
        for key in ("marginSummary", "crossMarginSummary"):
            summary = perp_after[key]
            for field in ("accountValue", "totalMarginUsed", "totalNtlPos", "totalRawUsd"):
                amount(summary[field], nonnegative=field in {"totalMarginUsed", "totalNtlPos"})
        amount(perp_after["crossMaintenanceMarginUsed"], nonnegative=True)
        amount(perp_after["withdrawable"])
        seen = set()
        for entry in sequence(perp_after["assetPositions"]):
            row = entry["position"]
            name = row["coin"]
            decode(str, name)
            if name in seen:
                raise ValueError("Duplicate position")
            seen.add(name)
            position = {"symbol": name, "quantity": amount(row["szi"]), "mark_price": marks.get(name),
                        "price_unit": PRICE_UNITS.get(name), "supported_asset": name in PRICE_UNITS,
                        "reported_position_value": amount(row["positionValue"], nonnegative=True),
                        "reported_value_unit": "venue-positionValue", "entry_price": row.get("entryPx"),
                        "unrealized_pnl": amount(row["unrealizedPnl"]), "margin_used": amount(row["marginUsed"], nonnegative=True),
                        "leverage": leverage(row["leverage"]), "valuation_multiplier": None, "notional_usd": None}
            if position["entry_price"] is not None:
                amount(position["entry_price"], nonnegative=True)
            if position["mark_price"] is None:
                issues.append("POSITION_MARK_UNKNOWN:" + name)
            result["positions"].append(position)
        positions_complete = not stale_perp
    except (ValueError, KeyError, TypeError, ArithmeticError):
        invalid("perp_after")
    orders_before, orders_after = get("orders_before"), get("orders_after")
    orders_complete = False
    try:
        seen = set()
        for row in sequence(orders_after):
            oid, name, side = row["oid"], row["coin"], row["side"]
            decode(int, oid)
            decode(str, name)
            if oid in seen or side not in {"B", "A"} or type(row["reduceOnly"]) is not bool or type(row["isTrigger"]) is not bool:
                raise ValueError("Duplicate/invalid order")
            seen.add(oid)
            decode(int, row["timestamp"])
            if row["timestamp"] > observations["orders_after"]["received_ms"]:
                raise ValueError("Future order")
            remaining, original = amount(row["sz"], nonnegative=True), amount(row["origSz"], nonnegative=True)
            if Decimal(remaining) > Decimal(original):
                raise ValueError("Order remaining exceeds original size")
            decode(str, row["orderType"])
            if row.get("cloid") is not None:
                decode(str, row["cloid"])
            if row.get("triggerPx") is not None:
                amount(row["triggerPx"], nonnegative=True)
            elif row["isTrigger"]:
                raise ValueError("Unknown trigger details")
            result["open_orders"].append({"order_id": str(oid), "symbol": name, "side": "buy" if side == "B" else "sell",
                                          "remaining_quantity": remaining, "original_quantity": original,
                                          "limit_price": amount(row["limitPx"], nonnegative=True),
                                          "reduce_only": row["reduceOnly"], "is_trigger": row["isTrigger"],
                                          "trigger_price": row.get("triggerPx"), "order_type": row.get("orderType"),
                                          "client_order_id": row.get("cloid"), "ownership": "unattributed",
                                          "status": "observed_open", "supported_asset": name in PRICE_UNITS})
        orders_complete = True
    except (ValueError, KeyError, TypeError, ArithmeticError):
        invalid("orders_after")
    for symbol in PRICE_UNITS:
        label = "active/" + symbol
        row = get(label)
        try:
            if row["coin"] != symbol or wallet_address(row["user"]) != wallet:
                raise ValueError("Active asset identity mismatch")
            available, sizes = row["availableToTrade"], row["maxTradeSzs"]
            if not isinstance(available, list) or not isinstance(sizes, list) or len(available) != 2 or len(sizes) != 2:
                raise ValueError("Invalid directional buying power")
            result["buying_power"][symbol] = {"available_to_trade": [amount(v, nonnegative=True) for v in available],
                                              "available_unit": "venue-availableToTrade",
                                              "max_trade_sizes": [amount(v, nonnegative=True) for v in sizes],
                                              "direction_mapping_verified": False, "size_unit": "venue-size",
                                              "mark_price": amount(row["markPx"], nonnegative=True),
                                              "leverage": leverage(row["leverage"]), "received_ms": observations[label]["received_ms"]}
        except (ValueError, KeyError, TypeError, ArithmeticError):
            invalid(label)
    # Compare endpoint state, ignoring only the reported time and list ordering. Still not an atomic snapshot.
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k != "time"}
        if isinstance(value, list):
            return sorted((stable(v) for v in value), key=canonical)
        return value

    pairs = ((spot_before, spot_after), (perp_before, perp_after), (orders_before, orders_after))
    matched = all(a is not None and b is not None and stable(a) == stable(b) for a, b in pairs)
    matched = matched and mode_before == mode_after and isinstance(role_after, dict) and role_after.get("role") == "user"
    result["consistency"] = "bracket_matched_non_atomic" if matched else "changed_or_incomplete"
    if not matched:
        issues.append("ACCOUNT_CHANGED_OR_INCOMPLETE")
    if isinstance(orders_before, list) and isinstance(orders_after, list):
        try:
            before = {str(row["oid"]): row for row in orders_before}
            after = {str(row["oid"]): row for row in orders_after}
            result["order_changes"] = {"appeared": sorted(after.keys() - before.keys()),
                                       "disappeared_unresolved": sorted(before.keys() - after.keys()),
                                       "changed": sorted(oid for oid in before.keys() & after.keys() if before[oid] != after[oid])}
        except (KeyError, TypeError):
            issues.append("ORDER_CHANGE_DETAILS_UNKNOWN")
    if bundle["ended_ms"] - bundle["started_ms"] > bundle["max_capture_ms"]:
        issues.append("CAPTURE_WINDOW_EXCEEDED")
    symbols = set(PRICE_UNITS) | {p["symbol"] for p in result["positions"]} | {o["symbol"] for o in result["open_orders"]}
    for symbol in sorted(symbols):
        quantity = next((p["quantity"] for p in result["positions"] if p["symbol"] == symbol), "0" if positions_complete else None)
        relevant = [o for o in result["open_orders"] if o["symbol"] == symbol]
        known = positions_complete and orders_complete and matched
        buys = sum((Decimal(o["remaining_quantity"]) for o in relevant if o["side"] == "buy"), Decimal(0)) if orders_complete else None
        sells = sum((Decimal(o["remaining_quantity"]) for o in relevant if o["side"] == "sell"), Decimal(0)) if orders_complete else None
        result["exposures"][symbol] = {"position_quantity": quantity, "pending_buy_quantity": str(buys) if buys is not None else None,
                                       "pending_sell_quantity": str(sells) if sells is not None else None,
                                       "minimum_quantity": str(Decimal(quantity) - sells) if known else None,
                                       "maximum_quantity": str(Decimal(quantity) + buys) if known else None,
                                       "known_observed_bounds": known, "includes_reduce_only_and_triggers": True,
                                       "notional_usd": None, "supported_asset": symbol in PRICE_UNITS}
        if symbol not in PRICE_UNITS:
            issues.append("UNSUPPORTED_EXPOSURE:" + symbol)
    result["par_positions_complete"], result["par_orders_complete"], result["balances_complete"] = positions_complete, orders_complete, balances_complete
    result["status"] = "observed" if not issues else "partial"
    result["issues"] = sorted(set(issues + ["USD_EQUITY_UNVERIFIED", "FREE_MARGIN_UNVERIFIED",
                                          "OTHER_DEX_EXPOSURE_UNKNOWN", "INFLIGHT_ORDERS_UNKNOWN"]))
    result["payload_digest"] = digest(result)
    return result
