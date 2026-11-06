"""Hourly next-OPEN *scenario* ledger, never a claim about historical fills/depth.

Execution at decision+1h avoids same-close fills. Prices are synthetic executable
prices built from real OHLC, explicit spread/slippage. Valuation is ASSUMED linear.
"""
import math
import statistics
from dataclasses import asdict, dataclass
from decimal import ROUND_DOWN, Decimal

from .data import HOUR


@dataclass(frozen=True)
class Costs:
    capital: float = 10000
    fee_bps: float = 9
    slippage_bps: float = 2
    spread_bps: float = 6  # full spread, each fill pays one half
    delay_hours: int = 1
    min_notional: float = 10

    def __post_init__(self):
        if (not all(math.isfinite(x) for x in (self.capital, self.fee_bps, self.slippage_bps,
                                              self.spread_bps, self.min_notional)) or self.capital <= 0
                or min(self.fee_bps, self.slippage_bps, self.spread_bps, self.min_notional) < 0
                or self.spread_bps / 2 + self.slippage_bps >= 10000 or self.fee_bps >= 10000
                or type(self.delay_hours) is not int or self.delay_hours < 1):
            raise ValueError("Finite nonnegative costs, capital>0 and next-hour delay required")


def quantity(notional, price, step):
    if step is None:
        raise ValueError("Unknown quantity step")
    units = (Decimal(str(abs(notional))) / Decimal(str(price)) / Decimal(str(step))).to_integral_value(
        rounding=ROUND_DOWN)
    return math.copysign(float(units * Decimal(str(step))), notional) if notional else 0


def replay(bars, funding, specs, orders, start, end, costs=Costs()):
    if start >= end:
        raise ValueError("Nonempty half-open simulation window required")
    by_time = {s: {b.time: b for b in rows} for s, rows in bars.items()}
    # open at T comes from a bar whose exclusive close is T+1h.
    opens = {s: {b.time - HOUR: b.open for b in rows} for s, rows in bars.items()}
    planned = {}
    signal_rows = []
    for order in orders:
        if not start <= order.time < end:
            continue
        if (sum(abs(v) for v in order.targets.values()) > 1 + 1e-9 or
                any(not math.isfinite(v) or abs(v) > .2 + 1e-9 for v in order.targets.values()) or
                any(s not in bars for s in order.targets)):
            raise ValueError("Research gross/single-asset budget or universe violation")
        scheduled = order.time + costs.delay_hours * HOUR
        if scheduled in planned and set(order.targets) & set(planned[scheduled]):
            raise ValueError("Conflicting same-time targets")
        planned.setdefault(scheduled, {}).update(order.targets)
        if sum(abs(v) for v in planned[scheduled].values()) > 1 + 1e-9:
            raise ValueError("Combined scheduled gross budget violated")
        signal_rows.append({**asdict(order), "scheduled_ms": scheduled})
    positions, last = {}, {}
    cash, fees, friction, funding_cash, gross_realized, turnover = costs.capital, 0., 0., 0., 0., 0.
    fills, trades, equity, rejected = [], [], [], []
    unknown_funding, stale_marks, batches = 0, 0, 0
    funding_rows = []
    peak, drawdown, gross_integral, max_gross = costs.capital, 0., 0., 0.

    def fill(symbol, target, reference, time, reason):
        nonlocal cash, fees, friction, gross_realized, turnover
        old = positions.get(symbol, {"q": 0., "avg": 0., "avg_ref": 0., "fee": 0., "funding": 0.,
                                     "opened": time})
        q0, delta = old["q"], target - old["q"]
        if abs(delta) < 1e-12:
            return False
        touch_cost = (costs.spread_bps / 2 + costs.slippage_bps) / 10000
        execution = reference * (1 + math.copysign(touch_cost, delta))
        fee = abs(delta * execution) * costs.fee_bps / 10000
        fees += fee
        friction += abs(delta) * abs(execution - reference)
        turnover += abs(delta * reference)
        cash -= fee
        remaining_fee = fee
        if q0 * delta < 0:
            closed = min(abs(q0), abs(delta))
            direction = math.copysign(1, q0)
            realized = direction * closed * (execution - old["avg"])
            gross = direction * closed * (reference - old["avg_ref"])
            gross_realized += gross
            cash += realized
            allocated_entry_fee = old["fee"] * closed / abs(q0)
            allocated_funding = old["funding"] * closed / abs(q0)
            exit_fee = fee * closed / abs(delta)
            trades.append({"symbol": symbol, "entry_ms": old["opened"], "exit_ms": time,
                           "quantity_closed": closed, "direction": direction,
                           "gross_price_pnl": gross, "price_pnl_after_friction": realized,
                           "fees": allocated_entry_fee + exit_fee, "funding_scenario": allocated_funding,
                           "net_scenario": realized - allocated_entry_fee - exit_fee + allocated_funding,
                           "reason": reason})
            old = {**old, "fee": old["fee"] - allocated_entry_fee,
                   "funding": old["funding"] - allocated_funding}
            remaining_fee -= exit_fee
        if not target:
            positions.pop(symbol, None)
        elif q0 * target <= 0:
            positions[symbol] = {"q": target, "avg": execution, "avg_ref": reference,
                                  "fee": remaining_fee, "funding": 0., "opened": time}
        elif abs(target) > abs(q0):
            added = abs(target) - abs(q0)
            positions[symbol] = {**old, "q": target,
                "avg": (abs(q0) * old["avg"] + added * execution) / abs(target),
                "avg_ref": (abs(q0) * old["avg_ref"] + added * reference) / abs(target),
                "fee": old["fee"] + remaining_fee}
        else:
            positions[symbol] = {**old, "q": target}
        fills.append({"symbol": symbol, "time_ms": time, "delta": delta, "target_quantity": target,
                      "reference": reference, "scenario_price": execution, "fee": fee,
                      "friction": abs(delta) * abs(execution - reference), "reason": reason})
        return True

    for time in range(start, end, HOUR):
        # Only observations at or before time may value a settlement inside this hour.
        for symbol, series in by_time.items():
            if time in series:
                last[symbol] = (time, series[time].close)
        targets = planned.get(time)
        if targets:
            missing = [s for s in targets if time not in opens[s] or not specs.get(s, {}).get("quantity_step")]
            if missing:
                rejected.append({"time_ms": time, "reason": "missing-basket-open-or-spec", "symbols": missing})
            else:
                touched = False
                for s, weight in sorted(targets.items()):
                    ref = opens[s][time]
                    desired = quantity(weight * costs.capital, ref, specs[s]["quantity_step"])
                    prior = positions.get(s, {}).get("q", 0)
                    # Below minimum reductions are permitted to close; new tiny changes are skipped.
                    if abs((desired - prior) * ref) < costs.min_notional and abs(desired) >= abs(prior):
                        if desired != prior:
                            rejected.append({"time_ms": time, "symbol": s, "reason": "minimum-notional"})
                        continue
                    touched |= fill(s, desired, ref, time, "scheduled-target")
                batches += int(touched)
        for symbol, position in list(positions.items()):
            points = [(t, rate) for t, rate in funding.get(symbol, {}).items() if time < t <= time + HOUR]
            # Coverage follows the nominal hourly bucket, while PAYMENTS follow exact timestamps.
            # A rate at exactly 'time' was settled before this hour's fill. The interval may
            # legitimately contain no payment if the next bucket settles a few ms after its end.
            if not any(t // HOUR == time // HOUR for t in funding.get(symbol, {})):
                unknown_funding += 1
            for settlement, rate in sorted(points):
                valuation = last.get(symbol)
                if valuation is None or settlement - valuation[0] > 2 * HOUR:
                    unknown_funding += 1
                    continue
                amount = -position["q"] * valuation[1] * rate
                cash += amount
                funding_cash += amount
                position["funding"] += amount
                funding_rows.append({"symbol": symbol, "time_ms": settlement, "rate": rate,
                                     "quantity": position["q"], "price_proxy": valuation[1],
                                     "price_proxy_time_ms": valuation[0], "cashflow_scenario": amount})
        for symbol, series in by_time.items():
            if time + HOUR in series:
                last[symbol] = (time + HOUR, series[time + HOUR].close)
        if time + HOUR == end:
            # Mandatory liquidation at the final close: observable price scenario, costs charged.
            for s in list(positions):
                if s in last and last[s][0] == end:
                    fill(s, 0, last[s][1], end, "terminal-close-scenario")
                else:
                    rejected.append({"time_ms": end, "symbol": s, "reason": "terminal-price-missing"})
        stale = [s for s in positions if s not in last or last[s][0] < time + HOUR]
        stale_marks += len(stale)
        unrealized = sum(p["q"] * (last[s][1] - p["avg"]) for s, p in positions.items() if s in last)
        value = cash + unrealized
        gross = sum(abs(p["q"] * last[s][1]) for s, p in positions.items() if s in last)
        net = sum(p["q"] * last[s][1] for s, p in positions.items() if s in last)
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak)
        gross_integral += gross / costs.capital
        max_gross = max(max_gross, gross / costs.capital)
        equity.append({"time_ms": time + HOUR, "equity_scenario": value,
                       "gross_fraction": gross / costs.capital, "net_fraction": net / costs.capital,
                       "stale_positions": len(stale)})
    complete = not positions and unknown_funding == 0 and stale_marks == 0
    final = equity[-1]["equity_scenario"] if equity else costs.capital
    # Cash accounting identity holds for closed books; unknown funding still disqualifies net completeness.
    if not positions and abs(final - costs.capital - gross_realized + friction + fees - funding_cash) > 1e-6:
        raise AssertionError("Ledger PnL reconciliation failed")
    wins = [t["net_scenario"] for t in trades]
    positive, negative = sum(max(0, x) for x in wins), -sum(min(0, x) for x in wins)
    daily = {}
    for row in equity:
        daily[(row["time_ms"] - 1) // (24 * HOUR)] = row["equity_scenario"]
    values, previous = [], costs.capital
    for value in daily.values():
        values.append(value / previous - 1)
        previous = value
    sharpe = (statistics.mean(values) / statistics.stdev(values) * math.sqrt(365)
              if len(values) >= 3 and statistics.stdev(values) else None)
    summary = {"signals": len(signal_rows), "entry_signals": sum(any(o["targets"].values()) for o in signal_rows),
        "fill_count": len(fills), "closed_segments": len(trades), "trade_batches": batches,
        "gross_price_pnl": gross_realized if not positions else None,
        "gross_return": gross_realized / costs.capital if not positions else None,
        "net_return": None, "executable_net_return": None,
        "net_scenario_return": (final - costs.capital) / costs.capital if complete else None,
        "known_components_return": (final - costs.capital) / costs.capital,
        "fees": fees, "spread_slippage_cost": friction, "funding_scenario": funding_cash,
        "funding_unknown_position_hours": unknown_funding, "stale_position_marks": stale_marks,
        "unclosed_positions": positions, "scenario_complete": complete,
        "win_rate_scenario": sum(x > 0 for x in wins) / len(wins) if wins and complete else None,
        "profit_factor_scenario": positive / negative if negative and complete else None,
        "profit_factor_infinite": bool(positive and not negative and complete),
        "max_drawdown_scenario": drawdown, "daily_sharpe_descriptive": sharpe,
        "return_days": len(values), "turnover_notional": turnover,
        "mean_gross_fraction": gross_integral / len(equity) if equity else 0,
        "max_gross_fraction": max_gross, "rejected_orders": len(rejected),
        "initial_capital": costs.capital, "costs": asdict(costs),
        "valuation": "ASSUMED linear q*price, NOT verified venue valuation",
        "execution": "OHLC next-open scenario, historical availability/depth UNKNOWN",
        "funding_valuation": "prior close proxy, NOT historical settlement oracle"}
    return {"summary": summary, "signals": signal_rows, "fills": fills, "trades": trades,
            "funding": funding_rows, "equity": equity, "rejections": rejected}
