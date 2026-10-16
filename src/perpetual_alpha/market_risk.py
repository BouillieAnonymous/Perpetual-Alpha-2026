"""Historical market statistics and sealed replay. No portfolio, account or execution authority."""
import copy
from dataclasses import asdict, dataclass
from decimal import Context, Decimal, localcontext

from .contracts import RiskSnapshot
from .historical.contracts import INTERVALS, PRICE_UNITS, SCHEMA, SOURCE, number
from .interfaces import timestamp
from .journal import canonical, digest

VERSION = "sample-covariance/0.1"
EVIDENCE_VERSION = "market-risk-replay/0.1"
MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class RiskConfig:
    symbols: tuple[str, ...]
    interval: str
    return_count: int
    min_samples: int
    max_age_ms: int

    def __post_init__(self):
        if not isinstance(self.symbols, (tuple, list)) or not 1 <= len(self.symbols) <= 10:
            raise ValueError("Explicit ordered universe of 1..10 assets required")
        if any(s not in PRICE_UNITS for s in self.symbols) or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Unsupported or duplicate market risk asset")
        object.__setattr__(self, "symbols", tuple(self.symbols))
        if self.interval not in INTERVALS:
            raise ValueError("Unsupported risk interval")
        if type(self.return_count) is not int or not 2 <= self.return_count <= 1000:
            raise ValueError("Research return count must be 2..1000")
        if type(self.min_samples) is not int or not 2 <= self.min_samples <= 1000:
            raise ValueError("Explicit research minimum must be 2..1000")
        timestamp(self.max_age_ms)

    def to_dict(self):
        return {**asdict(self), "symbols": list(self.symbols)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"symbols", "interval", "return_count", "min_samples", "max_age_ms"}:
            raise ValueError("Unexpected risk configuration fields")
        return cls(**value)


def freeze(store, config, asof_ms, *, synthetic):
    """Pin once, then read every asset at the same asof/sequence/anchor; never collect on demand."""
    timestamp(asof_ms)
    if type(synthetic) is not bool:
        raise ValueError("Explicit synthetic source declaration required")
    step = INTERVALS[config.interval]
    end = asof_ms // step * step
    start = end - (config.return_count + 1) * step
    if start < 0:
        raise ValueError("Risk window precedes epoch")
    pin = store.pin(asof_ms)
    histories = [store.read_candles(s, config.interval, start, end, asof_ms,
                                  max_records=config.return_count + 1, max_age_ms=config.max_age_ms,
                                  snapshot_seq=pin["snapshot_seq"], snapshot_anchor=pin["anchor"])
                 for s in config.symbols]
    return {"schema_version": EVIDENCE_VERSION, "model_version": VERSION, "asof_ms": asof_ms,
            "config": config.to_dict(), "pin": pin, "synthetic": synthetic, "histories": histories}


def _estimate(inputs):
    expected = {"schema_version", "model_version", "asof_ms", "config", "pin", "synthetic", "histories"}
    if not isinstance(inputs, dict) or set(inputs) != expected:
        raise ValueError("Unexpected frozen input fields")
    if inputs["schema_version"] != EVIDENCE_VERSION or inputs["model_version"] != VERSION:
        raise ValueError("Unsupported replay/model version")
    config = RiskConfig.from_dict(inputs["config"])
    asof = inputs["asof_ms"]
    timestamp(asof)
    pin = inputs["pin"]
    if set(pin) != {"asof_ms", "snapshot_seq", "anchor"} or pin["asof_ms"] != asof:
        raise ValueError("Invalid frozen pin")
    timestamp(pin["snapshot_seq"])
    if not isinstance(pin["anchor"], str) or len(pin["anchor"]) != 64 or any(c not in "0123456789abcdef" for c in pin["anchor"]):
        raise ValueError("Invalid pin anchor")
    if type(inputs["synthetic"]) is not bool:
        raise ValueError("Explicit source declaration required")
    histories = inputs["histories"]
    if not isinstance(histories, list) or len(histories) != len(config.symbols):
        raise ValueError("Risk evidence must cover the requested universe")
    step = INTERVALS[config.interval]
    end, samples = asof // step * step, config.return_count
    start = end - (samples + 1) * step
    if start < 0:
        raise ValueError("Invalid frozen window")
    base = dict(snapshot_id="market-risk:" + digest(inputs), asof_ms=asof, method="historical-sample-covariance",
                model_version=VERSION, symbols=config.symbols, estimation_window_ms=samples * step,
                return_interval_ms=step, simulated=inputs["synthetic"])
    errors, columns = [], []
    stale = False
    for s, h in zip(config.symbols, histories):
        if not isinstance(h, dict) or h.get("payload_digest") != digest({k: v for k, v in h.items() if k != "payload_digest"}):
            raise ValueError("Frozen history digest mismatch")
        if (h.get("schema_version"), h.get("source"), h.get("symbol"), h.get("interval"), h.get("price_unit"),
            h.get("asof_ms"), h.get("snapshot_seq"), h.get("start_ms"), h.get("end_ms")) != (
                SCHEMA, SOURCE, s, config.interval, PRICE_UNITS[s], asof, pin["snapshot_seq"], start, end):
            raise ValueError("Frozen history identity/window mismatch")
        # Overall status alone is insufficient: stale may coexist with gaps or invalid revisions.
        if h.get("status") == "stale" or asof - end > config.max_age_ms:
            stale = True
            errors.append("STALE:" + s)
        rows = h.get("records")
        complete = (h.get("status") == "ready" and h.get("gaps") == [] and h.get("invalid") == []
                    and h.get("truncated") is False and h.get("closed_only") is True
                    and h.get("not_yet_closed_records") == 0 and h.get("expected_records") == samples + 1
                    and h.get("available_records") == samples + 1 and isinstance(rows, list) and len(rows) == samples + 1)
        if not complete:
            errors.append("INCOMPLETE_HISTORY:" + s)
            continue
        closes = []
        try:
            for i, row in enumerate(rows):
                opened = start + i * step
                for key in ("open_ms", "close_ms", "received_ms", "available_ms", "revision_seq"):
                    timestamp(row[key])
                if (row["open_ms"] != opened or row["close_ms"] != opened + step
                        or not row["close_ms"] <= row["received_ms"] <= row["available_ms"] <= asof
                        or not 1 <= row["revision_seq"] <= pin["snapshot_seq"]):
                    raise ValueError("Invalid candle timing/revision")
                closes.append(Decimal(number(row["close"], positive=True)))
            columns.append([b / a - 1 for a, b in zip(closes, closes[1:])])
        except (ValueError, TypeError, KeyError, ArithmeticError):
            errors.append("INVALID_RECORD:" + s)
    diagnostics = {"correlation": [], "mean_returns": {}, "zero_volatility_symbols": [],
                   "matrix_invertibility": "not_assumed", "execution_authorized": False}

    def unavailable(status, codes):
        return {"risk_snapshot": RiskSnapshot(**base, status=status, sample_count=0,
                                              volatilities={}, covariance=(), errors=tuple(sorted(set(codes)))).to_dict(),
                "diagnostics": diagnostics}

    if errors:
        return unavailable("stale" if stale else "missing", errors)
    if samples < config.min_samples:
        return unavailable("missing", ["INSUFFICIENT_SAMPLES"])
    means = [sum(c) / samples for c in columns]
    centered = [[v - mean for v in c] for c, mean in zip(columns, means)]
    n = len(columns)
    matrix = [[Decimal(0)] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            matrix[i][j] = matrix[j][i] = sum(a * b for a, b in zip(centered[i], centered[j])) / (samples - 1)
    try:
        vols = [matrix[i][i].sqrt() for i in range(n)]
        snapshot = RiskSnapshot(**base, status="ready", sample_count=samples,
                                volatilities=dict(zip(config.symbols, map(str, vols))),
                                covariance=tuple(tuple(map(str, row)) for row in matrix))
        correlation = [[None if vols[i] == 0 or vols[j] == 0 else
                        str(Decimal(1) if i == j else matrix[i][j] / (matrix[i][i] * matrix[j][j]).sqrt())
                        for j in range(n)] for i in range(n)]
        if any(v is not None and abs(Decimal(v)) > 1 for row in correlation for v in row):
            raise ValueError("Numerical correlation bound exceeded")
    except (ValueError, ArithmeticError):
        return unavailable("error", ["NUMERICAL_VALIDATION_FAILED"])
    diagnostics.update(correlation=correlation, mean_returns=dict(zip(config.symbols, map(str, means))),
                       zero_volatility_symbols=[s for s, vol in zip(config.symbols, vols) if vol == 0])
    return {"risk_snapshot": snapshot.to_dict(), "diagnostics": diagnostics}


def estimate(inputs):
    """Deterministic decimal context independent of caller's precision/rounding/traps."""
    if len(canonical(inputs).encode()) > MAX_BYTES:
        raise ValueError("Risk input byte budget exceeded")
    with localcontext(Context(prec=28)):
        return _estimate(copy.deepcopy(inputs))


def seal(inputs):
    inputs = copy.deepcopy(inputs)
    result = estimate(inputs)
    evidence = {"inputs": inputs, "result": result, "input_digest": digest(inputs), "result_digest": digest(result)}
    evidence["artifact_digest"] = digest(evidence)
    if len(canonical(evidence).encode()) > MAX_BYTES:
        raise ValueError("Replay artifact byte budget exceeded")
    return evidence


def replay(evidence):
    if not isinstance(evidence, dict) or set(evidence) != {"inputs", "result", "input_digest", "result_digest", "artifact_digest"}:
        raise ValueError("Invalid replay envelope")
    if len(canonical(evidence).encode()) > MAX_BYTES:
        raise ValueError("Replay artifact byte budget exceeded")
    if (evidence["artifact_digest"] != digest({k: v for k, v in evidence.items() if k != "artifact_digest"})
            or evidence["input_digest"] != digest(evidence["inputs"])
            or evidence["result_digest"] != digest(evidence["result"])):
        raise ValueError("Replay digest mismatch")
    result = estimate(evidence["inputs"])
    if canonical(result) != canonical(evidence["result"]):
        raise ValueError("Replay result differs from frozen expectation")
    return result
