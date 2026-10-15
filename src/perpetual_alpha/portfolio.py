"""User-approved fixed-quantity simulation policy. No implicit exits or invented probabilities."""
from .context import validate_account
from .contracts import ForecastBatch, RiskSnapshot, positive_ms, symbol
from .interfaces import Decision, TargetPosition, decimal_string, identity, timestamp


class PortfolioUnavailable(ValueError):
    pass


class FixedQuantityPortfolio:
    def __init__(self, quantities, horizon_ms, max_age_ms, decision_ttl_ms, policy_version):
        if not isinstance(quantities, dict) or not quantities:
            raise ValueError("Explicit demonstration quantities required")
        self.quantities = {}
        for s, quantity in quantities.items():
            symbol(s)
            value = decimal_string(quantity)
            if value <= 0:
                raise ValueError("Demo quantities must be positive")
            self.quantities[s] = value
        positive_ms(horizon_ms)
        positive_ms(max_age_ms)
        positive_ms(decision_ttl_ms)
        identity(policy_version)
        self.horizon_ms, self.max_age_ms = horizon_ms, max_age_ms
        self.decision_ttl_ms, self.policy_version = decision_ttl_ms, policy_version

    def construct(self, batch, risk, context, now_ms, decision_id):
        timestamp(now_ms)
        batch = ForecastBatch.from_dict(batch.to_dict())
        risk = RiskSnapshot.from_dict(risk.to_dict())
        if context["protocol_version"] != "1.0" or batch.context_id != context["context_id"]:
            raise PortfolioUnavailable("Context identity/version mismatch")
        if not 0 <= now_ms - context["asof_ms"] <= self.max_age_ms:
            raise PortfolioUnavailable("Context stale or future")
        if any(d["required"] and d["status"] != "ready" for d in context["datasets"].values()):
            raise PortfolioUnavailable("Required dataset unavailable")
        try:
            validate_account(context["account"], now_ms, self.max_age_ms)
        except (ValueError, TypeError, KeyError) as error:
            raise PortfolioUnavailable("Account unavailable") from error
        if risk.status != "ready" or not risk.simulated or not 0 <= now_ms - risk.asof_ms <= self.max_age_ms:
            raise PortfolioUnavailable("Demo risk snapshot unavailable")
        if risk.return_interval_ms != self.horizon_ms:
            raise PortfolioUnavailable("Risk interval does not match forecast horizon")
        if not context["asof_ms"] <= batch.created_ms <= now_ms:
            raise PortfolioUnavailable("Forecast batch future or before context")
        targets = []
        expiries = [now_ms + self.decision_ttl_ms]
        for item in batch.items:
            if item.symbol not in self.quantities or item.symbol not in risk.symbols:
                raise PortfolioUnavailable("Unsupported forecast asset")
            if item.expires_ms <= now_ms or item.asof_ms != context["asof_ms"]:
                raise PortfolioUnavailable("Expired forecast or mismatched information cutoff")
            if item.horizon_ms != self.horizon_ms:
                raise PortfolioUnavailable("Mixed or unsupported forecast horizons")
            if item.status == "ready" and item.direction in {"up", "down"}:
                targets.append(TargetPosition(item.symbol, self.quantities[item.symbol] *
                                              (1 if item.direction == "up" else -1)))
                expiries.append(item.expires_ms)
        # Forecast producer owns control identity; the independent policy version is auditable in reason/journal.
        return Decision(decision_id, context["context_id"], batch.producer_id, batch.producer_version,
                        now_ms, min(expiries), tuple(targets),
                        "SIMULATED fixed quantities; policy=" + self.policy_version)
