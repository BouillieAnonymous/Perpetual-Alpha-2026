import math
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal


def number(value) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite exchange data")
    return result


@dataclass(frozen=True)
class Candle:
    time: int  # opening time, milliseconds UTC
    open: float
    high: float
    low: float
    close: float
    volume: float = 0


@dataclass(frozen=True)
class Market:
    time: int  # exchange order-book timestamp, NOT local receipt time
    bid: float
    ask: float
    mark: float
    oracle: float
    funding: float
    sz_decimals: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 10_000


@dataclass
class AccountState:
    equity: float
    position: float
    open_orders: list[dict] = field(default_factory=list)
    other_positions: list[str] = field(default_factory=list)


def size_down(size: float, decimals: int) -> float:
    return float(Decimal(str(abs(size))).quantize(Decimal(10) ** -decimals, rounding=ROUND_DOWN))


def limit_price(price: float, decimals: int, buy: bool) -> float:
    """Venue: <= 5 significant digits and <= 6-szDecimals fractional digits.

    Round inward: buy price never exceeds the requested slippage cap;
    sell price never falls below it. Integer prices are always valid.
    """
    from decimal import ROUND_CEILING, ROUND_FLOOR

    value = Decimal(str(price))
    if not value.is_finite() or value <= 0:
        raise ValueError("Invalid limit price")
    digits = max(0, min(6 - decimals, 4 - value.adjusted()))
    return float(value.quantize(Decimal(10) ** -digits, rounding=ROUND_FLOOR if buy else ROUND_CEILING))
