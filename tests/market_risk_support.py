"""Explicit synthetic history; no reconstruction of real market availability."""
from perpetual_alpha.historical.store import HistoricalStore
from perpetual_alpha.market_risk import RiskConfig, freeze

STEP = 60000
ASOF = 6 * STEP


def fixture(path, symbols=("par:BTC", "par:ETH"), closes=None):
    closes = closes or [["100", "110", "99", "99"], ["200", "220", "198", "198"]]
    store = HistoricalStore(path, clock=lambda: ASOF)
    for s, values in zip(symbols, closes):
        rows = [bar(s, (i + 2) * STEP, v) for i, v in enumerate(values)]
        store.record_candles("synthetic:" + s, s, "1m", 2 * STEP, ASOF, rows, ASOF)
    config = RiskConfig(symbols, "1m", 3, 3, STEP)
    return store, config, freeze(store, config, ASOF, synthetic=True)


def bar(symbol, opened, value):
    return {"t": opened, "T": opened + STEP - 1, "s": symbol, "i": "1m", "o": value,
            "h": value, "l": value, "c": value, "v": "1", "n": 1}
