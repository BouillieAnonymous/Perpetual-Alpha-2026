"""Explicit synthetic policy and full-exposure arithmetic; no live-account conversion."""
from dataclasses import asdict, dataclass
from decimal import Context, Decimal, localcontext
from functools import wraps
from itertools import product

from .context import validate_account
from .contracts import RiskSnapshot
from .historical.contracts import PRICE_UNITS
from .interfaces import decimal_string, identity, timestamp
from .journal import digest


def stable_decimal(function):
    @wraps(function)
    def run(*args, **kwargs):
        with localcontext(Context(prec=28)):
            return function(*args, **kwargs)
    return run


def amount(value, *, positive=False):
    if not isinstance(value, str) or len(value) > 128 or value.strip() != value:
        raise ValueError("Bounded decimal string required")
    n = decimal_string(value)
    if abs(n.adjusted()) > 50 or n < 0 or (positive and n == 0):
        raise ValueError("Invalid synthetic amount")
    return n


@dataclass(frozen=True)
class SimulationPolicy:
    version: str
    asset_budgets_usd: dict
    asset_caps_usd: dict
    gross_cap_usd: str
    net_cap_usd: str
    sigma_cap_usd: str
    leverage_cap: str
    margin_fraction: str
    margin_buffer_usd: str
    drawdown_cap: str
    session_loss_cap_usd: str
    horizon_ms: int
    decision_ttl_ms: int
    max_age_ms: int
    min_samples: int
    risk_model_version: str
    allow_reversals: bool
    allow_explicit_exits: bool
    mode: str = "synthetic-only"

    def __post_init__(self):
        identity(self.version, self.risk_model_version)
        if self.mode != "synthetic-only":
            raise ValueError("Only synthetic simulation policy is supported")
        if type(self.allow_reversals) is not bool or type(self.allow_explicit_exits) is not bool:
            raise ValueError("Explicit synthetic exit/reversal policy required")
        for mapping in (self.asset_budgets_usd, self.asset_caps_usd):
            if not isinstance(mapping, dict) or not 1 <= len(mapping) <= 10 or not set(mapping) <= PRICE_UNITS.keys():
                raise ValueError("Explicit supported asset budgets/caps required")
            for v in mapping.values():
                amount(v, positive=True)
        if not self.asset_budgets_usd.keys() <= self.asset_caps_usd.keys():
            raise ValueError("Budget assets must have independent limits")
        for key in ("gross_cap_usd", "net_cap_usd", "sigma_cap_usd", "leverage_cap", "margin_fraction",
                    "drawdown_cap", "session_loss_cap_usd"):
            amount(getattr(self, key), positive=True)
        amount(self.margin_buffer_usd)
        if amount(self.drawdown_cap) > 1 or amount(self.margin_fraction) > 1:
            raise ValueError("Synthetic fractions must be at most one")
        for key in ("horizon_ms", "decision_ttl_ms", "max_age_ms"):
            timestamp(getattr(self, key))
            if getattr(self, key) == 0:
                raise ValueError("Positive duration required")
        if type(self.min_samples) is not int or not 2 <= self.min_samples <= 1000:
            raise ValueError("Explicit research sample minimum required")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Missing or unknown simulation policy fields")
        return cls(**value)


@stable_decimal
def validate_state(state, policy, now):
    """Caller is a local synthetic harness, never the Agent HTTP API."""
    timestamp(now)
    expected = {"schema_version", "synthetic", "context_id", "asof_ms", "account", "assertions", "market", "risk",
                "control", "health", "peak_equity_usd", "session_start_equity_usd"}
    if not isinstance(state, dict) or set(state) != expected:
        raise ValueError("Explicit synthetic state required; real account observations are not accepted")
    if state["schema_version"] != "portfolio-simulation/0.1" or state["synthetic"] is not True:
        raise ValueError("Synthetic state required")
    identity(state["context_id"])
    timestamp(state["asof_ms"])
    if not 0 <= now - state["asof_ms"] <= policy.max_age_ms:
        raise ValueError("State stale or future")
    required = {"risk_ready", "global_account_complete", "other_exposure_known", "inflight_orders_known"}
    if not isinstance(state["assertions"], dict) or set(state["assertions"]) != required or any(
            v is not True for v in state["assertions"].values()):
        raise ValueError("Account risk scope unknown")
    account = validate_account(state["account"], now, policy.max_age_ms)
    if account["observed_ms"] > state["asof_ms"]:
        raise ValueError("Future account relative to state cutoff")
    equity = amount(account["equity_usd"], positive=True)
    amount(account["available_margin_usd"])
    peak = amount(state["peak_equity_usd"], positive=True)
    session = amount(state["session_start_equity_usd"], positive=True)
    if peak < max(equity, session):
        raise ValueError("Inconsistent synthetic peak equity")
    positions = {p["symbol"]: decimal_string(p["quantity"]) for p in account["positions"]}
    for q in positions.values():
        amount(str(abs(q)))
    orders = account["open_orders"]
    if len(orders) > 1000 or len(positions) > 10:
        raise ValueError("Account list budget exceeded")
    for order in orders:
        amount(order["remaining_quantity"])
    control = state["control"]
    if not isinstance(control, dict) or set(control) != {
            "producer_id", "producer_version", "epoch", "role", "symbols", "accepted_asof_ms", "consumed_asof_ms"}:
        raise ValueError("Explicit trusted control state required")
    identity(control["producer_id"], control["producer_version"])
    timestamp(control["epoch"])
    timestamp(control["accepted_asof_ms"])
    if control["accepted_asof_ms"] > state["asof_ms"]:
        raise ValueError("Future accepted watermark")
    if control["consumed_asof_ms"] is not None:
        timestamp(control["consumed_asof_ms"])
        if control["consumed_asof_ms"] > control["accepted_asof_ms"]:
            raise ValueError("Consumption watermark ahead of acceptance")
    if control["role"] != "control" or not isinstance(control["symbols"], list) or not control["symbols"]:
        raise ValueError("Only explicit control scope may propose decisions")
    if len(set(control["symbols"])) != len(control["symbols"]) or not set(control["symbols"]) <= policy.asset_budgets_usd.keys():
        raise ValueError("Control universe exceeds configured budget scope")
    if not isinstance(state["health"], dict) or set(state["health"]) != {"market_data", "account_data", "execution"}:
        raise ValueError("Explicit trusted health required")
    if any(v is not True for v in state["health"].values()):
        raise ValueError("Core data or execution unhealthy")
    risk = RiskSnapshot.from_dict(state["risk"])
    if (risk.status != "ready" or not risk.simulated or risk.model_version != policy.risk_model_version
            or risk.sample_count < policy.min_samples or risk.return_interval_ms != policy.horizon_ms
            or risk.asof_ms > state["asof_ms"] or not 0 <= now - risk.asof_ms <= policy.max_age_ms):
        raise ValueError("Synthetic market risk not usable under explicit policy")
    universe = set(positions) | {o["symbol"] for o in orders} | set(control["symbols"])
    if not universe <= set(risk.symbols) or not universe <= policy.asset_caps_usd.keys():
        raise ValueError("Risk/policy does not cover all held, ordered and controllable assets")
    market = state["market"]
    for s in universe:
        quote = market[s]
        if set(quote) != {"usd_per_unit", "quantity_step", "observed_ms", "event_ms", "valuation_source"}:
            raise ValueError("Explicit synthetic USD/unit and step required")
        if quote["valuation_source"] != "synthetic-fixture":
            raise ValueError("Real contract valuation has not been authorized/verified")
        amount(quote["usd_per_unit"], positive=True)
        amount(quote["quantity_step"], positive=True)
        timestamp(quote["event_ms"])
        timestamp(quote["observed_ms"])
        if not (0 <= now - quote["event_ms"] <= policy.max_age_ms
                and quote["event_ms"] <= quote["observed_ms"] <= state["asof_ms"]):
            raise ValueError("Synthetic valuation stale/future")
    return positions, risk


@stable_decimal
def exposure_metrics(state, policy, now, targets):
    positions, risk = validate_state(state, policy, now)
    if not set(targets) <= set(state["control"]["symbols"]):
        raise ValueError("Unauthorized target asset")
    symbols = sorted(set(positions) | set(targets) | {o["symbol"] for o in state["account"]["open_orders"]})
    bounds = {}
    for s in symbols:
        q = positions.get(s, Decimal(0))
        target = targets.get(s, q)
        amount(str(abs(target)))
        orders = [o for o in state["account"]["open_orders"] if o["symbol"] == s]
        buys = sum((amount(o["remaining_quantity"]) for o in orders if o["side"] == "buy"), Decimal(0))
        sells = sum((amount(o["remaining_quantity"]) for o in orders if o["side"] == "sell"), Decimal(0))
        price = amount(state["market"][s]["usd_per_unit"], positive=True)
        bounds[s] = ((min(q, target)-sells)*price, (max(q, target)+buys)*price)
    gross = sum((max(abs(lo), abs(hi)) for lo, hi in bounds.values()), Decimal(0))
    net = max(abs(sum((v[0] for v in bounds.values()), Decimal(0))),
              abs(sum((v[1] for v in bounds.values()), Decimal(0))))
    # A PSD quadratic reaches its maximum on a box at a vertex (at most 2**10).
    indices = [risk.symbols.index(s) for s in symbols]
    covariance = [[decimal_string(risk.covariance[i][j]) for j in indices] for i in indices]
    variance = Decimal(0)
    for corner in product(*(bounds[s] if bounds[s][0] != bounds[s][1] else (bounds[s][0],) for s in symbols)):
        value = sum((corner[i]*covariance[i][j]*corner[j] for i in range(len(symbols)) for j in range(len(symbols))), Decimal(0))
        if value < Decimal("-1e-12"):
            raise ValueError("Numerical portfolio variance invalid")
        variance = max(variance, value)
    equity = amount(state["account"]["equity_usd"], positive=True)
    current_gross = sum((abs(q)*amount(state["market"][s]["usd_per_unit"], positive=True) for s, q in positions.items()), Decimal(0))
    margin_left = amount(state["account"]["available_margin_usd"]) - max(Decimal(0), gross-current_gross)*amount(policy.margin_fraction)
    drawdown = (amount(state["peak_equity_usd"])-equity)/amount(state["peak_equity_usd"])
    loss = max(Decimal(0), amount(state["session_start_equity_usd"])-equity)
    sigma = variance.sqrt()
    breaches = []
    checks = (("GROSS", gross, policy.gross_cap_usd), ("NET", net, policy.net_cap_usd),
              ("SIGMA", sigma, policy.sigma_cap_usd), ("LEVERAGE", gross/equity, policy.leverage_cap),
              ("DRAWDOWN", drawdown, policy.drawdown_cap), ("SESSION_LOSS", loss, policy.session_loss_cap_usd))
    for name, value, cap in checks:
        if value > amount(cap):
            breaches.append(name)
    if margin_left < amount(policy.margin_buffer_usd):
        breaches.append("MARGIN_BUFFER")
    for s, (lo, hi) in bounds.items():
        if max(abs(lo), abs(hi)) > amount(policy.asset_caps_usd[s]):
            breaches.append("ASSET_CAP:"+s)
    return {"gross_usd": str(gross), "net_bound_usd": str(net), "sigma_bound_usd": str(sigma),
            "leverage_bound": str(gross/equity), "margin_left_usd": str(margin_left), "drawdown": str(drawdown),
            "session_loss_usd": str(loss), "usd_bounds": {s: list(map(str, v)) for s, v in bounds.items()},
            "breaches": sorted(breaches)}


def binding(state, policy):
    return {"state_digest": digest(state), "policy_digest": digest(policy.to_dict())}
