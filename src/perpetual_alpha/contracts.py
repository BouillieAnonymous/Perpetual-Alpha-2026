"""Internal, independently versioned contracts. No signing or network capabilities."""
from dataclasses import asdict, dataclass, fields
from typing import Any, Protocol

from .interfaces import DataRequest, decimal_string, identity, timestamp


def symbol(value):
    identity(value)
    if not value.startswith("par:") or len(value) <= 4:
        raise ValueError("Competition symbol required")


def positive_ms(value):
    timestamp(value)
    if value == 0:
        raise ValueError("Positive duration required")


class JsonContract:
    def to_dict(self):
        import json
        return json.loads(json.dumps(asdict(self), allow_nan=False))

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {f.name for f in fields(cls)}:
            raise ValueError("Missing or unexpected contract fields")
        return cls(**payload)


@dataclass(frozen=True)
class Forecast(JsonContract):
    symbol: str
    asof_ms: int
    expires_ms: int
    horizon_ms: int
    status: str
    direction: str | None
    evidence_refs: tuple[str, ...] = ()
    reason: str = ""
    expected_return: str | None = None
    prob_up: str | None = None
    prob_down: str | None = None
    forecast_vol: str | None = None
    quantiles: dict[str, str] | None = None
    distribution_ref: str | None = None
    calibration_id: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None

    @classmethod
    def from_dict(cls, payload):
        optional = {"reason", "expected_return", "prob_up", "prob_down", "forecast_vol", "quantiles",
                    "distribution_ref", "calibration_id", "model_version", "prompt_version"}
        expected = {f.name for f in fields(cls)}
        if not isinstance(payload, dict) or not expected - optional <= set(payload) <= expected:
            raise ValueError("Missing or unexpected forecast fields")
        return cls(**payload)

    def __post_init__(self):
        symbol(self.symbol)
        timestamp(self.asof_ms)
        timestamp(self.expires_ms)
        positive_ms(self.horizon_ms)
        if self.expires_ms <= self.asof_ms:
            raise ValueError("Forecast expiry must follow asof")
        if self.status not in {"ready", "abstain", "error"}:
            raise ValueError("Invalid forecast status")
        if (self.status == "ready" and self.direction not in {"up", "down", "neutral"}) or (
            self.status != "ready" and self.direction is not None
        ):
            raise ValueError("Direction must match forecast status")
        if not isinstance(self.evidence_refs, (tuple, list)):
            raise ValueError("Evidence must be a sequence")
        for ref in self.evidence_refs:
            identity(ref)
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        if not isinstance(self.reason, str):
            raise ValueError("Reason must be text")
        for value in (self.expected_return, self.prob_up, self.prob_down, self.forecast_vol):
            if value is not None:
                decimal_string(value)
        for value in (self.prob_up, self.prob_down):
            if value is not None and not 0 <= decimal_string(value) <= 1:
                raise ValueError("Probability must be in [0,1]")
        if self.prob_up is not None and self.prob_down is not None:
            if decimal_string(self.prob_up) + decimal_string(self.prob_down) > 1:
                raise ValueError("Probability masses exceed one")
        if self.forecast_vol is not None and decimal_string(self.forecast_vol) < 0:
            raise ValueError("Negative forecast volatility")
        if self.expected_return is not None and decimal_string(self.expected_return) < -1:
            raise ValueError("Simple return cannot be below -1")
        for value in (self.distribution_ref, self.calibration_id, self.model_version, self.prompt_version):
            if value is not None:
                identity(value)
        if self.quantiles is not None:
            if not isinstance(self.quantiles, dict) or not self.quantiles:
                raise ValueError("Quantiles must be a nonempty mapping")
            pairs = sorted((decimal_string(p), decimal_string(r)) for p, r in self.quantiles.items())
            if any(not 0 < p < 1 or r < -1 for p, r in pairs):
                raise ValueError("Invalid quantile")
            if len({p for p, _ in pairs}) != len(pairs) or any(
                a[1] > b[1] for a, b in zip(pairs, pairs[1:])
            ):
                raise ValueError("Quantiles must be unique and monotone")
        if self.status != "ready" and any(v is not None for v in (
            self.expected_return, self.prob_up, self.prob_down, self.forecast_vol,
            self.quantiles, self.distribution_ref,
        )):
            raise ValueError("Non-ready forecast cannot carry effective numeric predictions")


@dataclass(frozen=True)
class ForecastBatch(JsonContract):
    forecast_batch_id: str
    context_id: str
    producer_id: str
    producer_version: str
    created_ms: int
    items: tuple[Forecast, ...]
    schema_version: str = "forecast/0.1"

    def __post_init__(self):
        identity(self.forecast_batch_id, self.context_id, self.producer_id, self.producer_version)
        timestamp(self.created_ms)
        if self.schema_version != "forecast/0.1":
            raise ValueError("Unsupported forecast schema")
        if not isinstance(self.items, (tuple, list)) or any(not isinstance(i, Forecast) for i in self.items):
            raise ValueError("Forecast items required")
        object.__setattr__(self, "items", tuple(self.items))
        if len({i.symbol for i in self.items}) != len(self.items):
            raise ValueError("Duplicate forecast symbol")
        if any(i.asof_ms > self.created_ms or i.expires_ms <= self.created_ms for i in self.items):
            raise ValueError("Forecast time inconsistent with batch creation")

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("Forecast batch object with items array required")
        return super().from_dict({**payload, "items": tuple(Forecast.from_dict(i) for i in payload["items"])})


@dataclass(frozen=True)
class RiskSnapshot(JsonContract):
    snapshot_id: str
    asof_ms: int
    status: str
    method: str
    model_version: str
    symbols: tuple[str, ...]
    estimation_window_ms: int
    return_interval_ms: int
    sample_count: int
    volatilities: dict[str, str]
    covariance: tuple[tuple[str, ...], ...]
    units: str = "simple-return-per-interval"
    annualization: str = "none"
    errors: tuple[str, ...] = ()
    simulated: bool = True
    schema_version: str = "risk-snapshot/0.1"

    def __post_init__(self):
        identity(self.snapshot_id, self.method, self.model_version)
        timestamp(self.asof_ms)
        positive_ms(self.estimation_window_ms)
        positive_ms(self.return_interval_ms)
        if self.estimation_window_ms < self.return_interval_ms:
            raise ValueError("Risk window shorter than interval")
        if type(self.sample_count) is not int or self.sample_count < 0:
            raise ValueError("Invalid sample count")
        if self.schema_version != "risk-snapshot/0.1" or self.status not in {"ready", "missing", "stale", "error"}:
            raise ValueError("Invalid risk schema/status")
        if self.units != "simple-return-per-interval" or self.annualization != "none":
            raise ValueError("Only unannualized per-interval risk supported")
        if type(self.simulated) is not bool:
            raise ValueError("Simulation flag must be boolean")
        if not isinstance(self.symbols, (list, tuple)) or not isinstance(self.errors, (list, tuple)):
            raise ValueError("Risk symbols/errors must be sequences")
        for s in self.symbols:
            symbol(s)
        object.__setattr__(self, "symbols", tuple(self.symbols))
        object.__setattr__(self, "errors", tuple(self.errors))
        for error in self.errors:
            identity(error)
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Risk symbols must be nonempty and unique")
        if not isinstance(self.volatilities, dict) or not isinstance(self.covariance, (list, tuple)):
            raise ValueError("Risk matrix and volatility mapping required")
        if any(not isinstance(row, (list, tuple)) for row in self.covariance):
            raise ValueError("Covariance rows must be arrays")
        object.__setattr__(self, "covariance", tuple(tuple(row) for row in self.covariance))
        if self.status != "ready":
            if not self.errors or self.volatilities or self.covariance:
                raise ValueError("Unavailable risk must carry errors and no numeric estimates")
            return
        if self.sample_count < 2 or self.errors or set(self.volatilities) != set(self.symbols):
            raise ValueError("Incomplete ready risk snapshot")
        n = len(self.symbols)
        if len(self.covariance) != n or any(len(row) != n for row in self.covariance):
            raise ValueError("Risk matrix dimensions do not match ordered symbols")
        matrix = [[decimal_string(v) for v in row] for row in self.covariance]
        for i, s in enumerate(self.symbols):
            vol = decimal_string(self.volatilities[s])
            if vol < 0 or abs(matrix[i][i] - vol * vol) > decimal_string("0.000000000001"):
                raise ValueError("Volatility and covariance diagonal inconsistent")
            for j in range(n):
                if matrix[i][j] != matrix[j][i] or matrix[i][j] ** 2 > matrix[i][i] * matrix[j][j]:
                    raise ValueError("Invalid covariance symmetry or bound")
        # LDL decomposition checks positive semidefiniteness without a numerical dependency.
        lower = [[decimal_string("0") for _ in range(n)] for _ in range(n)]
        diagonal = [decimal_string("0") for _ in range(n)]
        tolerance = decimal_string("0.000000000001")
        for i in range(n):
            lower[i][i] = decimal_string("1")
            diagonal[i] = matrix[i][i] - sum(lower[i][k] ** 2 * diagonal[k] for k in range(i))
            if diagonal[i] < -tolerance:
                raise ValueError("Covariance is not positive semidefinite")
            for j in range(i + 1, n):
                residual = matrix[j][i] - sum(lower[j][k] * lower[i][k] * diagonal[k] for k in range(i))
                if abs(diagonal[i]) <= tolerance:
                    if abs(residual) > tolerance:
                        raise ValueError("Invalid singular covariance")
                else:
                    lower[j][i] = residual / diagonal[i]


@dataclass(frozen=True)
class RiskVerdict(JsonContract):
    verdict_id: str
    decision_id: str
    decision_digest: str
    rule_version: str
    checked_ms: int
    status: str
    reason_code: str
    account_snapshot_id: str
    market_snapshot_id: str
    risk_snapshot_id: str
    evidence: tuple[str, ...]
    simulated: bool = True
    schema_version: str = "risk-verdict/0.1"

    def __post_init__(self):
        from .interfaces import REASON_CODES
        identity(self.verdict_id, self.decision_id, self.decision_digest, self.rule_version,
                 self.account_snapshot_id, self.market_snapshot_id, self.risk_snapshot_id)
        if len(self.decision_digest) != 64 or any(c not in "0123456789abcdef" for c in self.decision_digest):
            raise ValueError("Canonical decision SHA-256 digest required")
        timestamp(self.checked_ms)
        if self.schema_version != "risk-verdict/0.1" or self.status not in {"approved", "rejected", "deferred"}:
            raise ValueError("Invalid verdict schema/status")
        if self.reason_code not in REASON_CODES or (self.status == "approved") != (self.reason_code == "OK"):
            raise ValueError("Inconsistent verdict reason")
        if type(self.simulated) is not bool or not isinstance(self.evidence, (list, tuple)) or not self.evidence:
            raise ValueError("Verdict must have evidence and a simulation flag")
        for item in self.evidence:
            identity(item)
        object.__setattr__(self, "evidence", tuple(self.evidence))


class ForecastProducer(Protocol):
    producer_id: str
    producer_version: str

    def requirements(self) -> tuple[DataRequest, ...]: ...
    def forecast(self, context: dict[str, Any]) -> ForecastBatch: ...


class MarketRiskEstimator(Protocol):
    def estimate(self, context: dict[str, Any]) -> RiskSnapshot: ...


class AgentAdapter(Protocol):
    """Reserved read-only prediction boundary; no real adapter configured in this release."""
    def forecast(self, context: dict[str, Any]) -> ForecastBatch: ...
