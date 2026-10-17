"""Offline sampled reference-basis research. No exchange or execution dependency."""
import argparse
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .contracts import Forecast, ForecastBatch
from .forecasting import RuleForecastProducer


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite number")
    return result


def direction(bid, ask, reference, threshold_bps):
    bid, ask, reference, threshold = map(number, (bid, ask, reference, threshold_bps))
    if not 0 < bid <= ask or reference <= 0 or threshold <= 0:
        raise ValueError("Invalid prices or threshold")
    if (reference / ask - 1) * 10000 >= threshold:
        return "up"
    if (bid / reference - 1) * 10000 >= threshold:
        return "down"
    return "neutral"


class BasisForecastProducer(RuleForecastProducer):
    """Register explicitly via ProducerRegistry; outputs forecasts, never orders."""
    producer_id = "reference-basis"
    producer_version = "0.1"

    def __init__(self, symbols, horizon_ms, ttl_ms, threshold_bps="20", **kwargs):
        super().__init__(symbols, horizon_ms, ttl_ms, **kwargs)
        self.threshold = number(threshold_bps)
        if self.threshold <= 0:
            raise ValueError("Positive threshold required")

    def forecast(self, context):
        now, items = context["asof_ms"], []
        for symbol in self.symbols:
            key = symbol + "_quotes"
            dataset = context["datasets"].get(key)
            signal, refs, reason = None, (), "Unavailable or stale quote"
            if dataset and dataset["status"] == "ready":
                row = dataset["records"][-1]
                if 0 <= now - row["event_ms"] <= self.ttl_ms and row["event_ms"] <= row["received_ms"] <= now:
                    data = row["data"]
                    signal = direction(data["bid"], data["ask"], data["oracle"], self.threshold)
                    refs, reason = (key,), "Oracle basis mean-reversion hypothesis; unhedged"
            items.append(Forecast(symbol, now, now + self.ttl_ms, self.horizon_ms,
                                  "ready" if signal else "abstain", signal, refs, reason))
        return ForecastBatch(context["context_id"] + ":" + self.producer_id, context["context_id"],
                             self.producer_id, self.producer_version,
                             self.clock() if self.clock else now, tuple(items))


@dataclass(frozen=True)
class ReplayConfig:
    threshold_bps: str = "20"
    fee_bps: str = "5"
    slippage_bps: str = "2"
    hold_ms: int = 300000
    latency_ms: int = 1000
    max_age_ms: int = 90000
    notional: str = "100"

    def __post_init__(self):
        for key in ("threshold_bps", "notional"):
            if number(getattr(self, key)) <= 0:
                raise ValueError("Positive parameter required")
        for key in ("fee_bps", "slippage_bps"):
            if number(getattr(self, key)) < 0:
                raise ValueError("Negative cost")
        for key in ("hold_ms", "latency_ms", "max_age_ms"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError("Positive integer time required")


def replay(rows, config=ReplayConfig()):
    """One position per symbol, next observed book fills, fixed holding time.

    Missing reference receipt timestamps are counted and excluded. Prices are
    depth-limited sampled estimates; funding is an explicit stress charge using
    absolute observed hourly rates, not reconstructed settlement cashflows.
    """
    positions, pending, last = {}, {}, {}
    trades, rejected, signals = [], 0, 0
    fee, slip = number(config.fee_bps) / 10000, number(config.slippage_bps) / 10000
    for row in rows:
        s, now, event = row["symbol"], row["received_ms"], row["event_ms"]
        if s not in {"par:BTC", "par:ETH"}:
            raise ValueError("Replay valuation only supports BTC/ETH")
        if any(type(t) is not int or t < 0 for t in (now, event)):
            raise ValueError("Invalid timestamp")
        if now <= last.get(s, -1):
            raise ValueError("Rows must have strictly increasing receipt time per symbol")
        last[s] = now
        ref_time = row.get("context_received_ms")
        if (ref_time is None or not 0 <= now - ref_time <= config.max_age_ms
                or not 0 <= now - event <= config.max_age_ms):
            rejected += 1
            pending.pop(s, None)
            continue
        bid, ask, oracle = (number(row[k]) for k in ("bid", "ask", "oracle"))
        signal = direction(bid, ask, oracle, config.threshold_bps)
        book = row["book"]
        if book["coin"] != s or book["time"] != event:
            raise ValueError("Book identity mismatch")
        levels = book["levels"]
        if number(levels[0][0]["px"]) != bid or number(levels[1][0]["px"]) != ask:
            raise ValueError("Book price mismatch")
        sizes = [number(levels[i][0]["sz"]) for i in (0, 1)]
        if min(sizes) <= 0:
            raise ValueError("Invalid depth")
        if s in positions:
            p = positions[s]
            if now - p["opened_ms"] >= config.hold_ms:
                side, q = p["side"], p["quantity"]
                price = bid * (1 - slip) if side == 1 else ask * (1 + slip)
                if q > sizes[0 if side == 1 else 1]:
                    continue
                costs = q * (p["entry"] + price) * fee
                funding = q * max(p["entry"], price) * max(p["funding"], abs(number(row["funding"])))
                funding *= Decimal(now - p["opened_ms"]) / 3600000
                pnl = side * q * (price - p["entry"]) - costs - funding
                trades.append(dict(symbol=s, opened_ms=p["opened_ms"], closed_ms=now,
                                   side=side, quantity=str(q), pnl=str(pnl), costs=str(costs),
                                   funding_stress=str(funding)))
                del positions[s]
            continue
        if s in pending:
            started, expected = pending[s]
            if now - started < config.latency_ms:
                continue
            del pending[s]
            if now - started > config.max_age_ms or signal != expected:
                continue
            side = 1 if signal == "up" else -1
            price = ask * (1 + slip) if side == 1 else bid * (1 - slip)
            q = number(config.notional) / price
            q = (q // Decimal("0.00001" if s == "par:BTC" else "0.0001")) * Decimal(
                "0.00001" if s == "par:BTC" else "0.0001")
            if q > 0 and q <= sizes[1 if side == 1 else 0]:
                positions[s] = dict(side=side, quantity=q, entry=price, opened_ms=now,
                                    funding=abs(number(row["funding"])))
        elif signal != "neutral":
            pending[s] = (now, signal)
            signals += 1
    return dict(rows=len(rows), rejected_time_rows=rejected, signals=signals, trades=trades,
                closed_trades=len(trades), open_positions=len(positions),
                realized_pnl=str(sum((number(t["pnl"]) for t in trades), Decimal(0))),
                status="incomplete" if positions else "sampled_estimate",
                limitations="Unhedged; sampled fills; open positions excluded from realized PnL; "
                            "funding stress only; no queue/latency guarantee or validated valuation adapter")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    result = {"input_sha256": hashlib.sha256(raw).hexdigest(), "schema": "basis-research/0.1",
              "experiments": []}
    # Chronological fixed halves; no automatic parameter selection.
    cutoff = (min(r["received_ms"] for r in rows) + max(r["received_ms"] for r in rows)) // 2
    for label, subset in (("first_half", [r for r in rows if r["received_ms"] < cutoff]),
                          ("second_half", [r for r in rows if r["received_ms"] >= cutoff])):
        for threshold in ("10", "20", "50"):
            config = ReplayConfig(threshold_bps=threshold)
            result["experiments"].append(dict(split=label, config=config.__dict__, result=replay(subset, config)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
