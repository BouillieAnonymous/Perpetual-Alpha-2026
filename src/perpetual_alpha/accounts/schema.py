"""Separate account-observation/0.1 schema, not an expansion of legacy trading contracts."""
import json
from pathlib import Path


def account_schema():
    text = {"type": "string", "minLength": 1, "maxLength": 4096}
    integer = {"type": "integer", "minimum": 0, "maximum": 2**53 - 1}
    decimal = {"type": "string", "maxLength": 128,
               "pattern": r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"}
    nullable_decimal = {"anyOf": [decimal, {"type": "null"}]}
    nullable_text = {"anyOf": [text, {"type": "null"}]}
    boolean = {"type": "boolean"}
    null = {"type": "null"}
    strings = {"type": "array", "items": text}

    def obj(properties, required=None):
        return {"type": "object", "properties": properties,
                "required": list(properties) if required is None else required, "additionalProperties": False}

    def mapping(item):
        return {"type": "object", "additionalProperties": item}

    balance = obj({"coin": text, "token": integer, "total": decimal, "hold": decimal,
                   "unheld_balance": decimal, "available_after_maintenance": nullable_decimal, "unit": text})
    position = obj({"symbol": text, "quantity": decimal, "mark_price": nullable_decimal, "price_unit": nullable_text,
                    "supported_asset": boolean, "reported_position_value": decimal, "reported_value_unit": text,
                    "entry_price": nullable_decimal, "unrealized_pnl": decimal, "margin_used": decimal,
                    "leverage": {"type": "object"}, "valuation_multiplier": null, "notional_usd": null})
    order = obj({"order_id": text, "symbol": text, "side": {"enum": ["buy", "sell"]},
                 "remaining_quantity": decimal, "original_quantity": decimal, "limit_price": decimal,
                 "reduce_only": boolean, "is_trigger": boolean, "trigger_price": nullable_decimal,
                 "order_type": nullable_text, "client_order_id": nullable_text,
                 "ownership": {"const": "unattributed"}, "status": {"const": "observed_open"}, "supported_asset": boolean})
    exposure = obj({"position_quantity": nullable_decimal, "pending_buy_quantity": nullable_decimal,
                    "pending_sell_quantity": nullable_decimal, "minimum_quantity": nullable_decimal,
                    "maximum_quantity": nullable_decimal, "known_observed_bounds": boolean,
                    "includes_reduce_only_and_triggers": {"const": True}, "notional_usd": null, "supported_asset": boolean})
    pair = {"type": "array", "minItems": 2, "maxItems": 2, "items": decimal}
    buying_power = obj({"available_to_trade": pair, "available_unit": text,
                        "max_trade_sizes": pair, "direction_mapping_verified": {"const": False}, "size_unit": text,
                        "mark_price": decimal, "leverage": {"type": "object"}, "received_ms": integer})
    component = obj({"status": {"enum": ["missing", "observed", "error", "invalid"]},
                     "received_ms": {"anyOf": [integer, null]}, "payload_digest": text, "event_ms": integer},
                    ["status", "received_ms"])
    schema = obj({"schema_version": {"const": "account-observation/0.1"}, "source": {"const": "hyperliquid-testnet/par"},
                  "wallet": {"type": "string", "pattern": "^0x[0-9a-f]{40}$"},
                  "wallet_identity_source": {"const": "operator-supplied-public-address"}, "team_ownership_verified": {"const": False},
                  "capture_id": text, "started_ms": integer, "observed_ms": integer,
                  "status": {"enum": ["observed", "partial", "unavailable"]}, "atomic": {"const": False},
                  "consistency": {"enum": ["unverified", "bracket_matched_non_atomic", "changed_or_incomplete"]},
                  "account_mode": nullable_text, "scope": text, "global_account_complete": {"const": False},
                  "other_dex_exposure": {"const": "unknown"}, "inflight_order_exposure": {"const": "unknown-no-intent-ledger"},
                  "equity_usd": null, "available_margin_usd": null, "risk_ready": {"const": False},
                  "balances": {"type": "array", "items": balance, "maxItems": 1000},
                  "positions": {"type": "array", "items": position, "maxItems": 1000},
                  "open_orders": {"type": "array", "items": order, "maxItems": 1000},
                  "buying_power": mapping(buying_power), "venue_perp_summary": {"type": ["object", "null"]},
                  "collateral": {"anyOf": [balance, null]}, "exposures": mapping(exposure), "components": mapping(component),
                  "par_positions_complete": boolean, "par_orders_complete": boolean, "balances_complete": boolean,
                  "order_changes": obj({"appeared": strings, "disappeared_unresolved": strings, "changed": strings}),
                  "issues": strings, "payload_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"}})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "AccountObservation", **schema}


if __name__ == "__main__":
    output = Path(__file__).resolve().parents[3] / "docs/schemas/AccountObservation.schema.json"
    output.write_text(json.dumps(account_schema(), indent=2) + "\n", encoding="utf-8")
