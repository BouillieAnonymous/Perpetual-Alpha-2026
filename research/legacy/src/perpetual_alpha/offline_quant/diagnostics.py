"""Forward labels ONLY for retrospective diagnostics, never fed back into signals."""
import math
import statistics

from .data import HOUR
from .relative import aligned_pair
from .signals import cross_section_orders, vol


def correlation(x, y):
    if len(x) < 3 or len(x) != len(y) or not statistics.stdev(x) or not statistics.stdev(y):
        return None
    return statistics.correlation(x, y)


def ranks(values):
    return [sum(v < x for v in values) + (sum(v == x for v in values) - 1) / 2 for x in values]


def diagnostics(bars, funding, model, symbols):
    maps = {s: {b.time: b for b in rows} for s, rows in bars.items()}
    rotations = cross_section_orders(bars, symbols, "risk", count=3 if len(symbols) == 10 else 1)
    ic = []
    for order in rotations:
        t = order.time
        # Same 1h delay as scenario engine; target label ends 24h after entry.
        if any(t + 2 * HOUR not in maps[s] or t + 26 * HOUR not in maps[s] for s in symbols):
            continue
        score = [order.features[s]["score"] for s in symbols]
        future = [math.log(maps[s][t + 26 * HOUR].open / maps[s][t + 2 * HOUR].open) for s in symbols]
        ic.append({"decision_ms": t, "spearman": correlation(ranks(score), ranks(future)),
                   "scores": dict(zip(symbols, score)), "future_price_returns": dict(zip(symbols, future))})
    funding_relation = {}
    for symbol in ("par:BTC", "par:ETH"):
        x, y, labels = [], [], []
        for time in sorted(maps[symbol]):
            if time // HOUR % 12 or time + 2 * HOUR not in maps[symbol] or time + 14 * HOUR not in maps[symbol]:
                continue
            known = [(t, r) for t, r in funding.get(symbol, {}).items() if t <= time]
            if not known:
                continue
            timestamp, rate = max(known)
            if time - timestamp > 2 * HOUR:
                continue
            future = math.log(maps[symbol][time + 14 * HOUR].open / maps[symbol][time + 2 * HOUR].open)
            x.append(rate)
            y.append(future)
            labels.append({"decision_ms": time, "known_rate": rate, "rate_time_ms": timestamp,
                           "future_price_return_12h": future})
        funding_relation[symbol] = {"observations": len(x), "pearson_rate_future_return": correlation(x, y),
                                    "labels": labels}
    stability = []
    if "beta" in model:
        cutoff = model["train_end_ms"]
        pairs = aligned_pair(bars, ("par:BTC", "par:ETH"))
        for label, sample in [("training", [(a, b) for a, b in pairs if a.time < cutoff]),
                              ("post_training_seen", [(a, b) for a, b in pairs if a.time >= cutoff])]:
            residuals = [math.log(b.close) - model["alpha"] - model["beta"] * math.log(a.close) for a, b in sample]
            stability.append({"period": label, "observations": len(residuals),
                              "mean_residual": statistics.mean(residuals) if residuals else None,
                              "std_residual": statistics.stdev(residuals) if len(residuals) > 1 else None,
                              "mean_z_frozen_train": statistics.mean(residuals) / model["residual_std"]
                              if residuals and model["residual_std"] else None})
    variability = {s: {"hourly_close_vol": vol(rows),
                       "zero_close_return_fraction": sum(a.close == b.close for a, b in zip(rows, rows[1:]))
                       / max(1, len(rows) - 1)} for s, rows in bars.items()}
    return {"xsm_ic_price_diagnostic": ic, "xsm_mean_ic": statistics.mean(
        x["spearman"] for x in ic if x["spearman"] is not None) if any(
        x["spearman"] is not None for x in ic) else None,
        "funding_rate_vs_price": funding_relation, "frozen_pair_residual_stability": stability,
        "asset_variability": variability,
        "warning": "few days, overlapping exposures, seen data; descriptive, no causal or significance claim"}
