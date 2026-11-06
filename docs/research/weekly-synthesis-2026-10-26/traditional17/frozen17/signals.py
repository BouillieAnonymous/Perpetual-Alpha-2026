"""Past-only, small fixed traditional baselines; no execution or network imports."""
import math
import statistics
from dataclasses import dataclass, field

from .data import HOUR, contiguous


def sign(x):
    return (x > 0) - (x < 0)


def returns(rows):
    return [math.log(b.close / a.close) for a, b in zip(rows, rows[1:])]


def vol(rows):
    r = returns(rows)
    return statistics.stdev(r) if len(r) >= 2 else 0.0


def quantile(values, fraction):
    return sorted(values)[int(fraction * (len(values) - 1))]


def efficiency(rows):
    r = returns(rows)
    return abs(sum(r)) / sum(map(abs, r)) if sum(map(abs, r)) else 0


def ema(values, n):
    value = values[0]
    for x in values[1:]:
        value += 2 / (n + 1) * (x - value)
    return value


@dataclass(frozen=True)
class Order:
    time: int
    targets: dict  # fractions of INITIAL capital, linear-price scenario only
    reason: str
    features: dict = field(default_factory=dict)


def single_orders(rows, strategy, *, hold=12, lookback=24, compression=168, regime=None, active_start=0):
    """One position per symbol. Fixed holds; MR exits are decided online, not on future extrema."""
    orders, state, expiry, reset = [], 0, 0, True
    symbol = rows[0].symbol if rows else ""
    sigma_history = []
    z_strategy = strategy in {"rolling_z", "bollinger"}
    for i in range(lookback + 1, len(rows)):
        segment = rows[i - lookback - 1:i + 1]
        if not contiguous(segment):
            sigma_history = []
            if state:
                orders.append(Order(rows[i].time, {symbol: 0}, "gap-exit-request"))
                state = 0
            continue
        recent, current, prior = rows[i - lookback:i + 1], rows[i], rows[i - 1]
        sigma = vol(recent)
        prior_sigma = vol(rows[i - lookback - 1:i])
        q = bool(len(sigma_history) >= compression + 1 and
                 sigma_history[-1] <= quantile(sigma_history[-compression - 1:-1], .2))
        expansion = abs(math.log(current.close / prior.close)) > 1.5 * prior_sigma
        # Thresholds exclude current bar, including the ATR range.
        channel = rows[i - lookback:i]
        breakout = 1 if current.close > max(r.high for r in channel) else (
            -1 if current.close < min(r.low for r in channel) else 0)
        tr = [max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
              for a, b in zip(rows[i - lookback - 1:i - 1], rows[i - lookback:i])]
        atr = statistics.mean(tr)
        price_history = [r.close for r in channel]
        deviation = statistics.stdev(price_history)
        z = (current.close - statistics.mean(price_history)) / deviation if deviation else 0
        momentum = math.log(current.close / rows[i - lookback].close)
        score = momentum / (prior_sigma * math.sqrt(lookback)) if prior_sigma else 0
        strength = efficiency(recent)
        # High/low classification uses strictly previous vol observations.
        high = len(sigma_history) >= 72 and sigma > quantile(sigma_history[-72:], .8)
        low = len(sigma_history) >= 72 and sigma <= quantile(sigma_history[-72:], .2)
        features = dict(sigma=sigma, prior_sigma=prior_sigma, compression=q, expansion=expansion,
                        breakout=breakout, z=z, score=score, efficiency=strength,
                        state="high_vol" if high else "low_vol" if low else
                              "trending" if strength > .35 else "range")
        sigma_history.append(sigma)
        if current.time < active_start:
            continue
        if state and (current.time >= expiry or z_strategy and abs(z) <= .5):
            orders.append(Order(current.time, {symbol: 0}, "fixed-or-z-exit", features))
            state = 0
            # No same-bar re-entry, even when the exit is delayed by the replay.
            continue
        if not breakout:
            reset = True
        if state:
            continue
        direction = 0
        if strategy.startswith("donchian"):
            direction = breakout if reset else 0
            if "compression" in strategy and not q:
                direction = 0
            if "expansion" in strategy and not expansion:
                direction = 0
        elif strategy == "atr":
            direction = sign(current.close - prior.close) if abs(current.close - prior.close) > 1.5 * atr else 0
        elif strategy == "ema":
            direction = sign(ema([r.close for r in recent], 6) - ema([r.close for r in recent], 24))
        elif strategy in {"tsm", "risk_momentum", "simple_mr"}:
            if current.time // HOUR % hold:
                continue
            direction = -sign(math.log(current.close / prior.close)) if strategy == "simple_mr" else sign(momentum)
        elif z_strategy:
            # Bollinger and rolling z with the SAME center/scale are intentionally equivalent.
            direction = -sign(z) if abs(z) > 2 else 0
        else:
            raise ValueError("Unknown traditional strategy")
        if regime == "trend" and (strength <= .35 or high):
            direction = 0
        if regime == "range" and (strength > .35 or high):
            direction = 0
        if regime == "cost" and prior_sigma * math.sqrt(hold) * 10000 <= 28:
            direction = 0
        if direction:
            # Same risk budget for VEX and its ablations. Floor prevents infinite quiet-market size.
            weight = min(.2, .005 / (max(prior_sigma, 1e-12) * math.sqrt(hold)))
            if strategy == "risk_momentum":
                weight *= min(1, abs(score))
            orders.append(Order(current.time, {symbol: direction * weight}, "entry", features))
            state, expiry = direction, current.time + hold * HOUR
            reset = False
    # Replay owns costed terminal liquidation. Never emit a second target at the last signal time.
    return orders


def cross_section_orders(bars, symbols, variant, *, lookback=24, risk_window=168, count=3):
    if not symbols or 2 * count > len(symbols):
        raise ValueError("Disjoint long/short sets required")
    maps = {s: {r.time: r for r in bars[s]} for s in symbols}
    common = sorted(set.intersection(*(set(m) for m in maps.values())))
    orders = []
    for t in common:
        if t // HOUR % 24:
            continue
        features = {}
        for symbol in symbols:
            times = [t - j * HOUR for j in range(risk_window, -1, -1)]
            if any(x not in maps[symbol] for x in times):
                break
            rows = [maps[symbol][x] for x in times]
            sigma = vol(rows)
            if not sigma:
                break
            ret = math.log(rows[-1].close / rows[-lookback - 1].close)
            features[symbol] = {"vol": sigma, "return": ret,
                                "score": ret / (sigma * math.sqrt(lookback))}
        if len(features) != len(symbols):
            continue  # retain existing positions; never silently shrink the universe
        if variant in {"tsm", "equal_long"}:
            raw = {s: sign(features[s]["return"]) / features[s]["vol"] if variant == "tsm" else 1
                   for s in symbols}
            if not any(raw.values()):
                orders.append(Order(t, {s: 0 for s in symbols}, "flat-momentum", features))
                continue
            scale = min(1 / sum(abs(v) for v in raw.values()), .2 / max(map(abs, raw.values())))
            targets = {s: raw[s] * scale for s in symbols}
        else:
            key = "return" if variant == "raw" else "score"
            ranked = sorted(symbols, key=lambda s: (features[s][key], s))
            groups = [(ranked[-count:], 1), (ranked[:count], -1)]
            sides = []
            for group, direction in groups:
                raw = {s: 1 / features[s]["vol"] if variant in {"risk", "reverse"} else 1 for s in group}
                side = {s: direction * .5 * raw[s] / sum(raw.values()) for s in group}
                scale = min(1, .2 / max(map(abs, side.values())))
                sides.append({s: v * scale for s, v in side.items()})
            budget = min(sum(map(abs, side.values())) for side in sides)
            targets = {s: 0 for s in symbols}
            for side in sides:
                total = sum(map(abs, side.values()))
                targets.update({s: v * budget / total * (-1 if variant == "reverse" else 1)
                                for s, v in side.items()})
        orders.append(Order(t, targets, "daily-rotation", features))
    return orders


def event_orders(rows, events, *, mode="window_pause", direction=1, base_weight=.1):
    """Only scheduled information published by the decision time is eligible.

    'surprise' requires actual release receipt AND a pre-release expectation vintage.
    No event list is generated from price jumps.
    """
    if mode not in {"window_pause", "surprise"}:
        raise ValueError("Unknown event rule")
    result = []
    for row in rows:
        weight = 0 if mode == "surprise" else direction * base_weight
        for event in events:
            if event["known_ms"] > row.time:
                continue
            if mode == "window_pause" and event["release_ms"] - HOUR <= row.time <= event["release_ms"] + HOUR:
                weight = 0
            if mode == "surprise":
                if (event.get("received_ms", math.inf) <= row.time and
                        event.get("expectation_available_ms", math.inf) < event["release_ms"] and
                        0 <= row.time - event["release_ms"] <= 2 * HOUR):
                    weight = base_weight * sign(event["actual"] - event["expected"])
                else:
                    weight = 0
        if mode == "surprise" and not events:
            weight = 0
        result.append(Order(row.time, {row.symbol: weight}, mode))
    return result


def lead_lag_feature(observations, decision_ms, *, max_age_ms):
    """As-of available time, never nearest event timestamp or a shifted future quote."""
    eligible = [r for r in observations if r["event_ms"] <= r["received_ms"] <= r["available_ms"] <= decision_ms
                and decision_ms - r["event_ms"] <= max_age_ms]
    if not eligible:
        return None
    row = max(eligible, key=lambda r: r["available_ms"])
    return row if decision_ms - row["received_ms"] <= max_age_ms else None
