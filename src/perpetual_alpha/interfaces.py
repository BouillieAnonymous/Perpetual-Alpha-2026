"""Strategy boundary only; intentionally no order submission implementation."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class TargetPosition:
    signal_id: str
    symbol: str
    quantity: Decimal  # signed asset quantity, NOT USD or margin
    created_ms: int
    expires_ms: int
    strategy_version: str
    reason: str


class Strategy(Protocol):
    def decide(self, market: list[dict], account: dict) -> list[TargetPosition]: ...


class Execution(Protocol):
    def apply(self, approved_target: TargetPosition) -> dict: ...
