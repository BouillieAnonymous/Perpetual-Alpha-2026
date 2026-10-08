"""Protocol 1.0: extensible data requests, fixed target-position decisions.

These types do not submit orders. Decimal quantities cross JSON boundaries as strings.
"""
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

PROTOCOL_VERSION = "1.0"
REASON_CODES = {
    "OK", "INVALID_INPUT", "UNSUPPORTED_VERSION", "EXPIRED", "FUTURE_SIGNAL", "ID_CONFLICT",
    "BUSY", "DATA_UNAVAILABLE", "ACCOUNT_UNAVAILABLE", "RISK_LIMIT", "ORDER_RULE", "VENUE_REJECTED",
    "NETWORK_UNKNOWN", "INTERNAL_ERROR",
}


def timestamp(value):
    if type(value) is not int or value < 0:
        raise ValueError("Timestamp must be nonnegative UTC epoch milliseconds")


def identity(*values):
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Nonempty string identity required")


def decimal_string(value):
    if not isinstance(value, str):
        raise ValueError("Quantity must be a decimal string, not a JSON number")
    quantity = Decimal(value)
    if not quantity.is_finite():
        raise ValueError("Non-finite quantity")
    return quantity


@dataclass(frozen=True)
class DataRequest:
    dataset_id: str
    source: str
    kind: str
    symbols: tuple[str, ...]
    fields: tuple[str, ...]
    lookback_ms: int
    max_records: int
    max_age_ms: int
    required: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        identity(self.dataset_id, self.source, self.kind)
        for value in (self.lookback_ms, self.max_age_ms):
            timestamp(value)
        if type(self.max_records) is not int or self.max_records < 1:
            raise ValueError("max_records must be a positive integer")
        if type(self.required) is not bool:
            raise ValueError("required must be boolean")
        if not isinstance(self.parameters, dict):
            raise ValueError("parameters must be a JSON object")
        json.dumps(self.parameters, allow_nan=False)


@dataclass(frozen=True)
class TargetPosition:
    symbol: str
    quantity: Decimal  # absolute signed target in asset units, NOT a trade delta

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol.startswith("par:") or not isinstance(self.quantity, Decimal):
            raise ValueError("Competition symbol and Decimal quantity required")
        if not self.quantity.is_finite():
            raise ValueError("Non-finite target")


@dataclass(frozen=True)
class Decision:
    decision_id: str
    context_id: str
    strategy_id: str
    strategy_version: str
    created_ms: int
    expires_ms: int
    targets: tuple[TargetPosition, ...]
    reason: str
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self):
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("Unsupported protocol version")
        identity(self.decision_id, self.context_id, self.strategy_id, self.strategy_version, self.reason)
        timestamp(self.created_ms)
        timestamp(self.expires_ms)
        if self.expires_ms <= self.created_ms:
            raise ValueError("Expiry must follow creation")
        if len({target.symbol for target in self.targets}) != len(self.targets):
            raise ValueError("Duplicate target symbol")

    def to_dict(self):
        return {
            "protocol_version": self.protocol_version, "decision_id": self.decision_id,
            "context_id": self.context_id, "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version, "created_ms": self.created_ms,
            "expires_ms": self.expires_ms, "reason": self.reason,
            "targets": [{"symbol": target.symbol, "quantity": str(target.quantity)} for target in self.targets],
        }

    @classmethod
    def from_dict(cls, payload):
        expected = {"protocol_version", "decision_id", "context_id", "strategy_id",
                    "strategy_version", "created_ms", "expires_ms", "reason", "targets"}
        if set(payload) != expected:
            raise ValueError("Missing or unexpected decision fields")
        if not isinstance(payload["targets"], list):
            raise ValueError("targets must be a list")
        targets = []
        for target in payload["targets"]:
            if set(target) != {"symbol", "quantity"}:
                raise ValueError("Invalid target fields")
            targets.append(TargetPosition(target["symbol"], decimal_string(target["quantity"])))
        return cls(**{**payload, "targets": tuple(targets)})


@dataclass(frozen=True)
class ExecutionItem:
    symbol: str
    target_quantity: Decimal
    actual_quantity: Decimal | None
    filled_quantity: Decimal
    order_ids: tuple[str, ...]
    error: str | None = None

    def __post_init__(self):
        TargetPosition(self.symbol, self.target_quantity)
        TargetPosition(self.symbol, self.filled_quantity)
        if self.actual_quantity is not None:
            TargetPosition(self.symbol, self.actual_quantity)


@dataclass(frozen=True)
class ExecutionReport:
    report_id: str
    decision_id: str
    sequence: int
    observed_ms: int
    status: str
    reason_code: str
    items: tuple[ExecutionItem, ...]
    message: str = ""
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self):
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("Unsupported protocol version")
        if self.status not in {"accepted", "running", "partial", "completed", "rejected", "failed", "unknown"}:
            raise ValueError("Invalid execution status")
        timestamp(self.observed_ms)
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Invalid report sequence")
        identity(self.report_id, self.decision_id, self.reason_code)
        if self.reason_code not in REASON_CODES:
            raise ValueError("Unsupported reason code")
        if self.status == "completed" and any(
            item.actual_quantity is None or item.actual_quantity != item.target_quantity or item.error
            for item in self.items
        ):
            raise ValueError("Completed report must confirm actual targets")

    def to_dict(self):
        return {
            "protocol_version": self.protocol_version, "report_id": self.report_id,
            "decision_id": self.decision_id, "sequence": self.sequence,
            "observed_ms": self.observed_ms, "status": self.status,
            "reason_code": self.reason_code, "message": self.message,
            "items": [{"symbol": item.symbol, "target_quantity": str(item.target_quantity),
                       "actual_quantity": str(item.actual_quantity) if item.actual_quantity is not None else None,
                       "filled_quantity": str(item.filled_quantity), "order_ids": list(item.order_ids),
                       "error": item.error} for item in self.items],
        }


class Strategy(Protocol):
    def requirements(self) -> tuple[DataRequest, ...]: ...
    def decide(self, context: dict[str, Any]) -> Decision: ...


class Execution(Protocol):
    def apply(self, decision: Decision) -> ExecutionReport: ...
    def reconcile(self, decision_id: str) -> ExecutionReport: ...
