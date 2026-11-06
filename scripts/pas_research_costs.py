"""Deterministic pre-trade cost estimate, conditional on convergence to reference.

The current half-spread is used as an explicit exit spread assumption. Unknown
future prices, funding changes and fills are not guaranteed by this calculation.
All values are per unit; bps are relative to the unadjusted entry touch price.
"""

from dataclasses import dataclass
from decimal import Decimal


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite number")
    return result


@dataclass(frozen=True)
class BasisCostModel:
    fee_bps: str = "9"
    slippage_bps: str = "2"
    min_net_edge_bps: str = "5"

    def __post_init__(self):
        for value in (self.fee_bps, self.slippage_bps, self.min_net_edge_bps):
            if number(value) < 0:
                raise ValueError("Costs and buffer must be nonnegative")
        if number(self.slippage_bps) >= 10000 or number(self.fee_bps) >= 10000:
            raise ValueError("Cost rate must be below 100 percent")

    def estimate(self, side, bid, ask, reference, funding_hourly, hold_ms):
        bid, ask, reference, funding = map(number, (bid, ask, reference, funding_hourly))
        if side not in ("up", "down") or not 0 < bid <= ask or reference <= 0:
            raise ValueError("Invalid side or prices")
        if type(hold_ms) is not int or hold_ms <= 0:
            raise ValueError("Positive holding period required")
        fee, slip = number(self.fee_bps) / 10000, number(self.slippage_bps) / 10000
        half_spread = (ask - bid) / (ask + bid)
        touch = ask if side == "up" else bid
        entry = touch * (1 + slip if side == "up" else 1 - slip)
        exit_touch = reference * (1 - half_spread if side == "up" else 1 + half_spread)
        exit_price = exit_touch * (1 - slip if side == "up" else 1 + slip)
        gross = reference - touch if side == "up" else touch - reference
        slippage = abs(entry - touch) + abs(exit_price - exit_touch)
        exit_spread = abs(reference - exit_touch)
        fees = (entry + exit_price) * fee
        funding_cost = max(entry, exit_price) * abs(funding) * Decimal(hold_ms) / 3600000
        costs = fees + slippage + exit_spread + funding_cost
        scale = Decimal(10000) / touch
        net_bps = (gross - costs) * scale
        return dict(
            gross_edge_bps=str(gross * scale),
            fee_bps=str(fees * scale),
            slippage_bps=str(slippage * scale),
            exit_spread_bps=str(exit_spread * scale),
            funding_bps=str(funding_cost * scale),
            total_cost_bps=str(costs * scale),
            net_edge_bps=str(net_bps),
            min_net_edge_bps=str(self.min_net_edge_bps),
            eligible=net_bps > number(self.min_net_edge_bps),
        )


def direction(bid, ask, reference, threshold_bps):
    """Executable-touch reference deviation; identical to the frozen basis prototype."""
    bid, ask, reference, threshold = map(number, (bid, ask, reference, threshold_bps))
    if not 0 < bid <= ask or reference <= 0 or threshold <= 0:
        raise ValueError("Invalid prices or threshold")
    if (reference / ask - 1) * 10000 >= threshold:
        return "up"
    if (bid / reference - 1) * 10000 >= threshold:
        return "down"
    return "neutral"
