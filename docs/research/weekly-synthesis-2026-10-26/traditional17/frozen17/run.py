import hashlib
from .signals import single_orders, cross_section_orders
from .relative import funding_orders, pair_orders

def orders_for(definition, bars, funding, model, start, end):
    symbols, kind = definition["symbols"], definition["kind"]
    truncated = {s: [r for r in bars[s] if r.time < end] for s in symbols}
    if kind == "none":
        return []
    if kind == "xsm":
        return cross_section_orders(truncated, symbols, definition["variant"], count=definition["count"],
                                    lookback=definition.get("lookback", 24))
    if kind == "funding":
        return funding_orders(truncated, funding, symbols, mode=definition["mode"],
                              rebalance=definition.get("rebalance", 12))
    if kind.startswith("pair_"):
        return pair_orders(truncated, symbols, model, ratio=kind == "pair_ratio",
                           gate=kind == "pair_coint", end=end)
    orders = [o for s in symbols for o in single_orders(truncated[s], kind, active_start=start,
            lookback=definition.get("lookback", 24), compression=definition.get("compression", 168),
            regime=definition.get("regime"))]
    if definition.get("regime") == "random":
        kept, filtered = {}, []
        for order in sorted(orders, key=lambda o: o.time):
            symbol = next(iter(order.targets))
            if any(order.targets.values()):
                draw = int(hashlib.sha256(f"20261018:{symbol}:{order.time}".encode()).hexdigest()[:8], 16)
                kept[symbol] = draw / 2**32 < .35
            if kept.get(symbol):
                filtered.append(order)
        return filtered
    return orders
