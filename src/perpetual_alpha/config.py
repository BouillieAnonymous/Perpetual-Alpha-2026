from dataclasses import dataclass
from datetime import date
from pathlib import Path
import math
import os
import re
import tomllib

URL = "https://api.hyperliquid-testnet.xyz"
DEX = "par"
COIN = "par:BTC"
HOUR = 3_600_000


@dataclass(frozen=True)
class Config:
    database: str = "data/practice.sqlite3"
    phase: str = "practice"
    poll_seconds: float = 30
    stale_seconds: float = 90
    allocation: float = 0.10
    daily_loss_limit: float = 0.02
    drawdown_limit: float = 0.05
    max_spread_bps: float = 20
    slippage_bps: float = 20
    scored_start: str = "2026-10-19"
    scored_end: str = "2026-11-06"
    minimum_volume: float | None = None

    def __post_init__(self):
        if self.phase not in {"practice", "scored"}:
            raise ValueError("phase must be practice or scored")
        for name in ("poll_seconds", "stale_seconds", "allocation", "daily_loss_limit",
                     "drawdown_limit", "max_spread_bps", "slippage_bps"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid {name}")
        if self.allocation > 0.10 or self.daily_loss_limit > 0.02 or self.drawdown_limit > 0.05:
            raise ValueError("v1 caps: allocation 10%, daily loss 2%, drawdown 5%")
        if self.slippage_bps > 100 or self.max_spread_bps > 100:
            raise ValueError("Price protection cannot exceed 100 bps")
        if self.poll_seconds < 5:
            raise ValueError("Polling must be at least 5 seconds")
        if date.fromisoformat(self.scored_start) > date.fromisoformat(self.scored_end):
            raise ValueError("Invalid scored date range")
        if self.minimum_volume is not None and (
            not math.isfinite(self.minimum_volume) or self.minimum_volume <= 0
        ):
            raise ValueError("minimum_volume must be positive when confirmed")


def load_config(path: str) -> Config:
    with open(path, "rb") as handle:
        return Config(**tomllib.load(handle)["bot"])


def load_env(path: str = ".env") -> None:
    """Load only two known names; never evaluate shell expressions or log values."""
    if not Path(path).exists():
        return
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"TEAM_WALLET_ADDRESS", "API_WALLET_PRIVATE_KEY"}:
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def team_address(required: bool = False) -> str | None:
    value = os.getenv("TEAM_WALLET_ADDRESS", "").strip()
    if not value and not required:
        return None
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ValueError("Set a valid public TEAM_WALLET_ADDRESS in .env")
    return value.lower()
