from dataclasses import dataclass
HOUR = 3600000

@dataclass
class Bar:
    symbol: str
    time: int  # exclusive close; not historical availability
    open: float
    high: float
    low: float
    close: float
    volume: float
    received: int

def contiguous(rows):
    return all(b.time - a.time == HOUR for a, b in zip(rows, rows[1:]))
