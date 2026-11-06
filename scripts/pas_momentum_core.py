"""ETH-only causal minute-grid momentum/reversal research; no orders or credentials."""

import math
import statistics
from bisect import bisect_left, bisect_right
from collections import Counter
from decimal import ROUND_FLOOR, Decimal

MINUTE = 60000
CUTOFF = 1792452948184


def corr(x, y):
    return (
        statistics.correlation(x, y) if len(x) > 2 and statistics.pstdev(x) and statistics.pstdev(y) else None
    )


def distribution(values):
    return dict(
        n=len(values),
        mean=statistics.mean(values) if values else None,
        median=statistics.median(values) if values else None,
        positive_fraction=sum(x > 0 for x in values) / len(values) if values else None,
    )


def features(quotes):
    ts = [q["t"] for q in quotes]
    grid, rejected = [], Counter()

    def asof(t):
        i = bisect_right(ts, t) - 1
        return quotes[i] if i >= 0 and t - ts[i] <= 90000 else None

    for t in range((ts[0] // MINUTE + 1) * MINUTE, ts[-1] // MINUTE * MINUTE + 1, MINUTE):
        history = [asof(t - j * MINUTE) for j in range(31)]
        if not all(history):
            rejected["warmup_or_stale"] += 1
            continue
        if any(b["event"] >= a["event"] for a, b in zip(history, history[1:])):
            rejected["nonadvancing_book_event"] += 1
            continue
        mids = [(q["bid"] + q["ask"]) / 2 for q in history]
        returns = [math.log(a / b) for a, b in zip(mids, mids[1:])]
        sigma = statistics.stdev(returns)
        if sigma <= 0:
            rejected["zero_volatility"] += 1
            continue
        ret = math.log(mids[0] / mids[5])
        q = history[0]
        grid.append(
            dict(
                t=t,
                q=q,
                z=ret / (sigma * math.sqrt(5)),
                past5_bps=ret * 10000,
                sigma_bps=sigma * 10000,
                spread_bps=(q["ask"] - q["bid"]) / mids[0] * 10000,
                liquidity=min(q["sizes"]) * mids[0],
            )
        )
    return grid, dict(rejected)


def timing(point, quotes, ts, horizon):
    i = bisect_left(ts, point["t"] + 1000)
    if i >= len(quotes) or ts[i] - point["t"] > 90000:
        return None
    j = bisect_left(ts, ts[i] + horizon * MINUTE)
    if j >= len(quotes) or ts[j] - (ts[i] + horizon * MINUTE) > 90000:
        return None
    return i, j


def fit(rows):
    x, y = [r["z"] for r in rows], [r["future_bps"] for r in rows]
    if len(x) < 30 or not statistics.pvariance(x):
        return dict(n=len(x), intercept=None, beta=None)
    beta = sum((a - statistics.mean(x)) * (b - statistics.mean(y)) for a, b in zip(x, y)) / sum(
        (a - statistics.mean(x)) ** 2 for a in x
    )
    return dict(n=len(x), intercept=statistics.mean(y) - beta * statistics.mean(x), beta=beta)


def relationship(rows, field="z"):
    return dict(
        n=len(rows),
        pearson=corr([r[field] for r in rows], [r["future_bps"] for r in rows]),
        future_mid=distribution([r["future_bps"] for r in rows]),
        momentum_signed_mid=distribution(
            [(1 if r["z"] > 0 else -1) * r["future_bps"] for r in rows if abs(r["z"]) >= 1]
        ),
    )


def simulate(points, quotes, model, horizon, end, kind, fee=9, slip=2, filtered=False):
    ts = [q["t"] for q in quotes]
    trades, counts = [], Counter()
    busy = -1
    for p in points:
        if p["t"] > end - (horizon * MINUTE + 180000):
            counts["window_end_embargo"] += 1
            continue
        if p["t"] <= busy or abs(p["z"]) < 1:
            continue
        side = (1 if p["z"] > 0 else -1) * (1 if kind == "momentum" else -1)
        if filtered:
            if model["beta"] is None:
                counts["model_unavailable"] += 1
                continue
            pred = side * (model["intercept"] + model["beta"] * p["z"])
            cost = p["spread_bps"] + 2 * fee + 2 * slip + p["q"]["funding"] * horizon / 60 * 10000 + 5
            if pred <= cost:
                counts["expected_cost_block"] += 1
                continue
        pair = timing(p, quotes, ts, horizon)
        if pair is None:
            counts["missing_entry_or_exit_timing"] += 1
            continue
        i, j = pair
        a = quotes[i]
        touch = a["ask"] if side == 1 else a["bid"]
        entry = touch * (1 + side * slip / 10000)
        step = Decimal(10) ** (-a["decimals"])
        qty = float(
            (Decimal("100") / Decimal(str(entry)) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        )
        if qty * entry < 10 or qty > a["sizes"][1 if side == 1 else 0]:
            counts["entry_depth_or_minimum"] += 1
            continue
        # Entry is accepted independently of future exit depth; wait until depth permits.
        while j < len(quotes) and qty > quotes[j]["sizes"][0 if side == 1 else 1]:
            j += 1
        if j >= len(quotes) or quotes[j]["t"] > end:
            counts["unresolved_exit"] += 1
            busy = end
            break
        b = quotes[j]
        close_touch = b["bid"] if side == 1 else b["ask"]
        exit = close_touch * (1 - side * slip / 10000)
        gross = side * qty * (close_touch - touch)
        fees = qty * (entry + exit) * fee / 10000
        friction = gross - side * qty * (exit - entry)
        funding = (
            qty
            * max(entry, exit)
            * max(q["funding"] for q in quotes[i : j + 1])
            * (b["t"] - a["t"])
            / 3600000
        )
        net = gross - fees - friction - funding
        trades.append(
            dict(
                decision_ms=p["t"],
                entry_ms=a["t"],
                exit_ms=b["t"],
                side=side,
                z=p["z"],
                qty=qty,
                entry=entry,
                exit=exit,
                entry_touch=touch,
                exit_touch=close_touch,
                gross_touch=gross,
                fees=fees,
                slippage=friction,
                funding_stress=funding,
                net=net,
            )
        )
        busy = b["t"]
    return dict(
        n=len(trades),
        net=sum(t["net"] for t in trades),
        gross_touch=sum(t["gross_touch"] for t in trades),
        fees=sum(t["fees"] for t in trades),
        slippage=sum(t["slippage"] for t in trades),
        funding_stress=sum(t["funding_stress"] for t in trades),
        win_rate=sum(t["net"] > 0 for t in trades) / len(trades) if trades else None,
        counters=dict(counts),
        trades=trades,
    )
