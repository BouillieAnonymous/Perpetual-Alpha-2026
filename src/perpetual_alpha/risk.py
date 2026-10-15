"""Independent demonstration risk gate. Limits are explicit simulation configuration only."""
from .context import validate_account
from .contracts import RiskSnapshot, RiskVerdict, positive_ms, symbol
from .interfaces import Decision, decimal_string, identity, timestamp
from .journal import digest


class DemoRiskGate:
    def __init__(self, quantity_limits, gross_limit_usd, max_age_ms, rule_version):
        if not isinstance(quantity_limits, dict) or not quantity_limits:
            raise ValueError("Explicit simulation limits required")
        self.quantity_limits = {}
        for s, value in quantity_limits.items():
            symbol(s)
            limit = decimal_string(value)
            if limit <= 0:
                raise ValueError("Quantity limit must be positive")
            self.quantity_limits[s] = limit
        self.gross_limit = decimal_string(gross_limit_usd)
        if self.gross_limit <= 0:
            raise ValueError("Gross limit must be positive")
        positive_ms(max_age_ms)
        identity(rule_version)
        self.max_age_ms, self.rule_version = max_age_ms, rule_version

    def check(self, decision, account, market, risk, now_ms):
        timestamp(now_ms)
        decision = Decision.from_dict(decision.to_dict())
        risk = RiskSnapshot.from_dict(risk.to_dict())
        account_id = "account:" + digest(account)
        market_id = "market:" + digest(market)

        def verdict(status, code, message):
            return RiskVerdict(decision.decision_id + ":risk:" + str(now_ms), decision.decision_id,
                               digest(decision.to_dict()), self.rule_version, now_ms, status, code,
                               account_id, market_id, risk.snapshot_id, (message,))

        if decision.created_ms > now_ms:
            return verdict("rejected", "FUTURE_SIGNAL", "Decision created in the future")
        if decision.expires_ms <= now_ms:
            return verdict("rejected", "EXPIRED", "Decision expired")
        try:
            validate_account(account, now_ms, self.max_age_ms)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            return verdict("deferred", "ACCOUNT_UNAVAILABLE", "Account freshness/amounts cannot be verified")
        if account["open_orders"]:
            return verdict("deferred", "BUSY", "Open orders cannot be simulated safely")
        if risk.status != "ready" or not risk.simulated or not 0 <= now_ms - risk.asof_ms <= self.max_age_ms:
            return verdict("deferred", "DATA_UNAVAILABLE", "Simulation risk unavailable")
        positions = {p["symbol"]: decimal_string(p["quantity"]) for p in account["positions"]}
        targets = {t.symbol: t.quantity for t in decision.targets}
        if not set(positions) | set(targets) <= set(self.quantity_limits):
            return verdict("rejected", "ORDER_RULE", "Unsupported held or targeted asset")
        if not set(positions) | set(targets) <= set(risk.symbols):
            return verdict("deferred", "DATA_UNAVAILABLE", "Risk does not cover full portfolio")
        prices = {}
        try:
            for s in set(positions) | set(targets):
                quote = market[s]
                timestamp(quote["observed_ms"])
                timestamp(quote["event_ms"])
                if not (0 <= now_ms - quote["observed_ms"] <= self.max_age_ms
                        and 0 <= now_ms - quote["event_ms"] <= self.max_age_ms):
                    raise ValueError("Stale/future quote")
                price, bid, ask = (decimal_string(quote[f]) for f in ("mark", "bid", "ask"))
                if price <= 0 or not 0 < bid <= ask:
                    raise ValueError("Invalid price/book")
                prices[s] = price
        except (ValueError, TypeError, KeyError, ArithmeticError):
            return verdict("deferred", "DATA_UNAVAILABLE", "Latest full-portfolio quote unavailable")
        projected = {**positions, **targets}
        reducing = bool(targets) and all(
            abs(q) <= abs(positions.get(s, 0)) and q * positions.get(s, 0) >= 0
            for s, q in targets.items()) and any(abs(q) < abs(positions.get(s, 0)) for s, q in targets.items())
        gross = sum(abs(q) * prices[s] for s, q in projected.items())
        if not reducing and (gross > self.gross_limit or any(
            abs(q) > self.quantity_limits[s] for s, q in projected.items())):
            return verdict("rejected", "RISK_LIMIT", "Explicit demo gross/quantity limit exceeded")
        if not reducing and any(abs(q) > abs(positions.get(s, 0)) or q * positions.get(s, 0) < 0
                                for s, q in targets.items()) and decimal_string(account["available_margin_usd"]) <= 0:
            return verdict("rejected", "RISK_LIMIT", "No verified available margin for increasing risk")
        return verdict("approved", "OK", "SIMULATED checks passed; no production margin/liquidation model")
