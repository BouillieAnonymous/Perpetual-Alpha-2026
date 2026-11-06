"""Fixed-training residual model and funding baselines. Optional statistics stay offline."""
import math
import statistics
import warnings

from .data import HOUR, contiguous
from .signals import Order, sign


def aligned_pair(bars, symbols):
    left, right = ({r.time: r for r in bars[s]} for s in symbols)
    return [(left[t], right[t]) for t in sorted(set(left) & set(right))]


def fit_pair(bars, symbols, train_end):
    pairs = [(a, b) for a, b in aligned_pair(bars, symbols) if a.time < train_end]
    # Fit on the longest continuous past segment; do not pretend missing hours are adjacent.
    segments = [[]]
    for pair in pairs:
        if segments[-1] and pair[0].time - segments[-1][-1][0].time != HOUR:
            segments.append([])
        segments[-1].append(pair)
    pairs = max(segments, key=len)
    if len(pairs) < 96:
        return {"status": "INSUFFICIENT_DATA", "reason": "fewer-than-96-contiguous-training-hours"}
    x, y = [math.log(a.close) for a, _ in pairs], [math.log(b.close) for _, b in pairs]
    variance = statistics.variance(x)
    if not variance:
        return {"status": "INSUFFICIENT_DATA", "reason": "zero-training-variance"}
    beta = sum((a - statistics.mean(x)) * (b - statistics.mean(y)) for a, b in zip(x, y)) / (
        (len(x) - 1) * variance)
    alpha = statistics.mean(y) - beta * statistics.mean(x)
    residual = [b - alpha - beta * a for a, b in zip(x, y)]
    lag, now = residual[:-1], residual[1:]
    denominator = sum((a - statistics.mean(lag)) ** 2 for a in lag)
    if denominator < 1e-20 or statistics.stdev(residual) < 1e-12:
        return {"status": "INSUFFICIENT_DATA", "reason": "degenerate-residual"}
    phi = sum((a - statistics.mean(lag)) * (b - statistics.mean(now)) for a, b in zip(lag, now)) / denominator
    result = {"alpha": alpha, "beta": beta, "residual_mean": statistics.mean(residual),
              "residual_std": statistics.stdev(residual), "phi": phi,
              "half_life_hours": -math.log(2) / math.log(phi) if 0 < phi < 1 else None,
              "fit_first_ms": pairs[0][0].time, "fit_last_ms": pairs[-1][0].time,
              "train_end_ms": train_end, "observations": len(pairs), "status": "INCONCLUSIVE"}
    try:
        from statsmodels.tsa.stattools import adfuller, coint, kpss
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter("always")
            result.update(coint_pvalue=float(coint(y, x, trend="c", autolag="aic")[1]),
                          adf_log_prices=[float(adfuller(z, autolag="AIC")[1]) for z in (x, y)],
                          adf_log_differences=[float(adfuller([b - a for a, b in zip(z, z[1:])],
                                                             autolag="AIC")[1]) for z in (x, y)],
                          residual_kpss_pvalue=float(kpss(residual, regression="c", nlags="auto")[1]))
            result["warnings"] = [str(n.message) for n in notices]
        integrated = (all(p > .05 for p in result["adf_log_prices"]) and
                      all(p < .05 for p in result["adf_log_differences"]))
        accepted = (integrated and result["coint_pvalue"] < .05 and result["residual_kpss_pvalue"] >= .05
                    and result["half_life_hours"] is not None and result["half_life_hours"] < 48
                    and beta > 0)
        result.update(status="EXPLORATORY_GATE_PASS" if accepted else "REJECTED",
                      reason="training-gate-only-not-stability-proof" if accepted else "stationarity-or-half-life-gate-failed")
    except ImportError:
        result.update(status="INSUFFICIENT_DATA", reason="optional-statsmodels-not-installed")
    return result


def pair_orders(bars, symbols, model, *, ratio=False, gate=True, end=None):
    if gate and model.get("status") != "EXPLORATORY_GATE_PASS":
        return []
    if "alpha" not in model:
        return []
    pairs = aligned_pair(bars, symbols)
    beta, alpha = (1., 0.) if ratio else (model["beta"], model["alpha"])
    orders, state, expiry = [], 0, 0
    for i, (a, b) in enumerate(pairs):
        if a.time < model["train_end_ms"] or end is not None and a.time >= end:
            continue
        if ratio:
            history = pairs[max(0, i - 72):i]
            if len(history) < 72 or not contiguous([p[0] for p in history] + [a]):
                continue
            values = [math.log(y.close / x.close) for x, y in history]
            center, scale = statistics.mean(values), statistics.stdev(values)
        else:
            center, scale = model["residual_mean"], model["residual_std"]
        if not scale:
            continue
        z = (math.log(b.close) - alpha - beta * math.log(a.close) - center) / scale
        if state and (abs(z) <= .5 or a.time >= expiry):
            orders.append(Order(a.time, {s: 0 for s in symbols}, "pair-exit", {"z": z}))
            state = 0
        elif not state and abs(z) > 2:
            state, expiry = -sign(z), a.time + 48 * HOUR
            # gross <= .4, each asset <= .2; beta is a log-return hedge, not risk neutrality.
            scale_w = .2 / max(1, abs(beta))
            orders.append(Order(a.time, {symbols[0]: -state * beta * scale_w,
                                         symbols[1]: state * scale_w}, "pair-entry", {"z": z, "beta": beta}))
    return orders


def funding_orders(bars, funding, symbols, *, mode="last", rebalance=12):
    orders = []
    times = sorted(set.intersection(*({b.time for b in bars[s]} for s in symbols)))
    for time in times:
        if time // HOUR % rebalance:
            continue
        rates = {}
        for symbol in symbols:
            known = sorted((t, r) for t, r in funding.get(symbol, {}).items() if t <= time)
            if not known or time - known[-1][0] > 2 * HOUR:
                break
            values = [r for t, r in known if time - 24 * HOUR < t <= time]
            if mode == "mean24" and len(values) < 24:
                break
            rates[symbol] = statistics.mean(values) if mode == "mean24" else known[-1][1]
        if len(rates) != len(symbols):
            continue
        if mode == "hedged":
            if len(symbols) != 2:
                raise ValueError("Hedged funding baseline requires two assets")
            side = sign(rates[symbols[1]] - rates[symbols[0]])
            targets = {symbols[0]: .2 * side, symbols[1]: -.2 * side}
        else:
            targets = {s: -.2 * sign(rates[s]) for s in symbols}
        orders.append(Order(time, targets, "funding-" + mode, {"known_rates": rates}))
    return orders
