"""Transactional local synthetic Paper venue. No network, signing, or external-order reconciliation."""
import copy
import json
from dataclasses import replace
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

from ..interfaces import Decision, ExecutionItem, ExecutionReport, decimal_string, identity
from ..journal import canonical, digest, report_from_dict
from ..portfolio_policies import BudgetPortfolio
from ..risk_gate import SimulationRiskGate
from ..risk_policy import SimulationPolicy, amount, exposure_metrics, stable_decimal, validate_state
from .contracts import ProcessingRecord, transition_processing
from .jobs import JobQueue
from .outbox import Outbox
from .supervisor import RiskSupervisor

TERMINAL = {"completed", "rejected", "failed", "partial-cancelled"}
ACTIVE = {"pending", "partial", "unknown"}


class PaperEngine:
    @stable_decimal
    def __init__(self, store, config, *, fault=None):
        self.store, self.config, self.fault = store, copy.deepcopy(config), fault or (lambda point: None)
        if set(config) != {"configuration_id", "model_version", "scope", "policy", "initial_state", "execution"}:
            raise ValueError("Exact synthetic Paper configuration required")
        if config["model_version"] != "paper-fixed-liquidity/0.1":
            raise ValueError("Unsupported Paper execution model")
        identity(config["configuration_id"], config["scope"])
        self.policy = SimulationPolicy.from_dict(config["policy"])
        state = config["initial_state"]
        validate_state(state, self.policy, state["asof_ms"])
        model = config["execution"]
        if set(model) != {"fee_bps", "slippage_bps", "max_fill_quantities", "min_notional_usd", "order_timeout_ms"}:
            raise ValueError("Explicit bounded Paper fill model required")
        if amount(model["fee_bps"]) > 1000 or amount(model["slippage_bps"]) >= 10000:
            raise ValueError("Paper fee/slippage engineering bound exceeded")
        if type(model["order_timeout_ms"]) is not int or not 1 <= model["order_timeout_ms"] <= 300000:
            raise ValueError("Bounded Paper intent timeout required")
        for key in ("max_fill_quantities", "min_notional_usd"):
            if set(model[key]) != set(self.policy.asset_caps_usd):
                raise ValueError("Fill rules must cover all configured assets")
            for s, value in model[key].items():
                amount(value, positive=key == "max_fill_quantities")
                if key == "max_fill_quantities" and amount(value) < amount(state["market"][s]["quantity_step"]):
                    raise ValueError("Paper liquidity below one step")
        with store.transaction() as (c, now):
            if c.execute("SELECT 1 FROM mock_configurations LIMIT 1").fetchone():
                raise ValueError("Mock and Paper modes require separate runtime databases")
            old = c.execute("SELECT * FROM paper_configuration WHERE id=1").fetchone()
            if old:
                if old["digest"] != digest(config) or digest(json.loads(old["payload"])) != old["digest"]:
                    raise ValueError("Paper configuration immutable; inspect before changing DB/policy")
            else:
                if c.execute("SELECT 1 FROM jobs LIMIT 1").fetchone() or c.execute("SELECT 1 FROM mock_plans LIMIT 1").fetchone():
                    raise ValueError("Use a separate empty runtime for Paper mode")
                c.execute("INSERT INTO paper_configuration VALUES(1,?,?,?)", (config["scope"], canonical(config), digest(config)))
                meta = {"cash": state["account"]["equity_usd"], "peak": state["peak_equity_usd"],
                        "session_start": state["session_start_equity_usd"], "paused": True,
                        "reasons": ["STARTUP_REQUIRES_EXPLICIT_RESUME"], "generation": "not-booted", "pause_epoch": 0}
                c.execute("INSERT INTO paper_meta VALUES(1,?)", (canonical(meta),))
                for p in state["account"]["positions"]:
                    c.execute("INSERT INTO paper_positions VALUES(?,?,?)", (p["symbol"], p["quantity"], p["mark_price"]))
                self._publish(c, now)
        self.outbox = Outbox(store)

    @staticmethod
    def _meta(c):
        return json.loads(c.execute("SELECT payload FROM paper_meta WHERE id=1").fetchone()[0])

    @staticmethod
    def _save_meta(c, meta):
        c.execute("UPDATE paper_meta SET payload=? WHERE id=1", (canonical(meta),))

    def _pause(self, c, reason):
        m = self._meta(c)
        if not m["paused"] or reason not in m["reasons"]:
            m.update(paused=True, reasons=sorted(set(m["reasons"]+[reason])), pause_epoch=m["pause_epoch"]+1)
            self._save_meta(c, m)

    def _publish(self, c, now):
        for s, quote in self.config["initial_state"]["market"].items():
            q = {**quote, "event_ms": now, "observed_ms": now}
            c.execute("INSERT INTO paper_quotes VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload",
                      (s, canonical(q)))

    @stable_decimal
    def publish_fixture(self):
        """Explicit synthetic tick from configured static prices, never a live-market timestamp refresh."""
        with self.store.transaction() as (c, now):
            self._publish(c, now)

    @stable_decimal
    def boot(self):
        with self.store.transaction() as (c, now):
            m = self._meta(c)
            m.update(generation=str(uuid4()), paused=True, reasons=sorted(set(m["reasons"]+["CORE_RESTART"])),
                     pause_epoch=m["pause_epoch"]+1)
            self._save_meta(c, m)
            # Requeue only reservations that never created an execution plan; preserve all Paper intents.
            rows = c.execute("SELECT p.record,o.delivery_epoch FROM processing p JOIN outbox o USING(processing_id) "
                             "LEFT JOIN paper_plans x USING(processing_id) WHERE o.status='running' AND x.processing_id IS NULL").fetchall()
            for row in rows:
                record = transition_processing(ProcessingRecord.from_dict(json.loads(row["record"])), "recover", now)
                Outbox._write(c, record, "pending", row["delivery_epoch"]+1)
            self.store.event(c, now, "paper-boot-paused", {"generation": m["generation"]})
        self.verify_ledger()

    def _state(self, c, now, job=None, *, exclude_decision=None):
        state, m = copy.deepcopy(self.config["initial_state"]), self._meta(c)
        state["market"] = {r["symbol"]: json.loads(r["payload"]) for r in c.execute("SELECT * FROM paper_quotes")}
        positions, unrealized, gross = [], Decimal(0), Decimal(0)
        for row in c.execute("SELECT * FROM paper_positions ORDER BY symbol"):
            q, entry = decimal_string(row["quantity"]), decimal_string(row["entry_price"])
            mark = amount(state["market"][row["symbol"]]["usd_per_unit"], positive=True)
            unrealized += q*(mark-entry)
            gross += abs(q)*mark
            positions.append({"symbol": row["symbol"], "quantity": str(q), "mark_price": str(mark)})
        equity = decimal_string(m["cash"])+unrealized
        orders = copy.deepcopy(state["account"]["open_orders"])
        unknown = False
        for row in c.execute("SELECT * FROM paper_intents WHERE status IN ('pending','partial','unknown')"):
            unknown |= row["status"] == "unknown"
            if row["decision_id"] == exclude_decision:
                continue  # This immutable Decision already includes its own exact remaining target path.
            left = decimal_string(row["quantity"])-decimal_string(row["filled"])
            orders.append({"order_id": row["intent_id"], "symbol": row["symbol"], "side": "buy" if left > 0 else "sell",
                           "remaining_quantity": str(abs(left)), "reduce_only": False})
        state.update(context_id=job.context_id if job else "paper-supervision", asof_ms=now,
                     peak_equity_usd=str(max(decimal_string(m["peak"]), equity)), session_start_equity_usd=m["session_start"])
        state["account"] = {"observed_ms": now, "status": "ready", "equity_usd": str(equity),
                            "available_margin_usd": str(max(Decimal(0), equity-gross*amount(self.policy.margin_fraction))),
                            "positions": positions, "open_orders": orders}
        state["assertions"]["inflight_orders_known"] = not unknown
        control = c.execute("SELECT * FROM controls WHERE scope=?", (self.config["scope"],)).fetchone()
        if control:
            state["control"].update(producer_id=control["producer_id"], producer_version=control["producer_version"],
                                     epoch=control["epoch"], accepted_asof_ms=control["accepted_asof_ms"] or 0,
                                     consumed_asof_ms=control["consumed_asof_ms"])
        if job:
            state["context_id"] = job.context_id
        # A generated synthetic covariance fixture, not newly observed historical statistics.
        state["risk"].update(asof_ms=job.asof_ms if job else now,
                              snapshot_id="paper-fixture-risk:"+str(job.asof_ms if job else now))
        return state

    def _supervisor(self, c):
        s, m = RiskSupervisor(self.policy), self._meta(c)
        s.paused, s.reasons = m["paused"], m["reasons"]
        return s

    def _supervise(self, c, now):
        try:
            state = self._state(c, now)
        except (ValueError, KeyError, TypeError, ArithmeticError):
            self._pause(c, "STATE_UNAVAILABLE")
            return {"paused": True, "current_failures": ["STATE_UNAVAILABLE"], "metrics": None, "automatic_actions": []}
        m = self._meta(c)
        m["peak"] = str(max(decimal_string(m["peak"]), decimal_string(state["account"]["equity_usd"])))
        self._save_meta(c, m)
        state["peak_equity_usd"] = m["peak"]
        report = self._supervisor(c).inspect(state, now)
        for reason in report["current_failures"]:
            self._pause(c, reason)
        return report

    @staticmethod
    def _proposal(plan):
        value = json.loads(plan["proposal"])
        if digest(value) != plan["proposal_digest"]:
            raise ValueError("Paper proposal digest mismatch")
        return {k: v for k, v in value.items() if k != "paper_initial_positions"}

    @staticmethod
    def _fence(c, plan):
        row = c.execute("SELECT status,delivery_epoch FROM outbox WHERE processing_id=?", (plan["processing_id"],)).fetchone()
        if row is None or row["status"] != "running" or row["delivery_epoch"] != plan["delivery_epoch"]:
            raise ValueError("Paper delivery fence changed; operator inspection required")

    @stable_decimal
    def stage(self, delivery):
        with self.store.transaction() as (c, now):
            old = c.execute("SELECT * FROM paper_plans WHERE processing_id=?", (delivery.processing.processing_id,)).fetchone()
            if old:
                self._proposal(old)
                return report_from_dict(json.loads(old["report"]))
            job = JobQueue._job(c, delivery.processing.job_id)[1]
            if job.control_scope != self.config["scope"] or job.role != "control":
                raise ValueError("Paper configuration cannot execute another control scope/role")
            from ..contracts import ForecastBatch
            accepted = ForecastBatch.from_dict(json.loads(c.execute("SELECT payload FROM submissions WHERE job_id=?", (job.job_id,)).fetchone()[0]))
            if canonical(accepted.to_dict()) != canonical(delivery.batch.to_dict()):
                raise ValueError("Delivery differs from accepted immutable Forecast")
            row = c.execute("SELECT status,delivery_epoch FROM outbox WHERE processing_id=?", (delivery.processing.processing_id,)).fetchone()
            if row["status"] != "running" or row["delivery_epoch"] != delivery.delivery_epoch:
                raise ValueError("Stale Paper delivery")
            if Outbox._invalid(c, job, accepted, now):
                raise ValueError("Stale or expired analysis cannot create Paper intent")
            state = self._state(c, now, job)
            decision_id = "paper-decision:"+digest({"processing_id": delivery.processing.processing_id})
            proposal = BudgetPortfolio(self.policy).construct(accepted, state, now, decision_id, analysis_asof_ms=job.asof_ms)
            approval = SimulationRiskGate(self.policy, self._supervisor(c)).recheck_execution(proposal, state, now)
            if approval["verdict"]["status"] != "approved":
                raise ValueError("Paper approval unavailable")
            decision = Decision.from_dict(proposal["decision"])
            proposal["paper_initial_positions"] = state["account"]["positions"]
            c.execute("INSERT INTO paper_plans VALUES(?,?,?,?,?,'running','OK',NULL,0,?)",
                      (delivery.processing.processing_id, decision_id, delivery.delivery_epoch, canonical(proposal), digest(proposal), now))
            c.execute("INSERT INTO paper_checks(decision_id,observed_ms,payload) VALUES(?,?,?)",
                      (decision_id, now, canonical({"approval": approval, "state": state, "phase": "intent-acceptance"})))
            current = {p["symbol"]: decimal_string(p["quantity"]) for p in state["account"]["positions"]}
            for target in decision.targets:
                q = current.get(target.symbol, Decimal(0))
                legs = (-q, target.quantity) if q*target.quantity < 0 else (target.quantity-q,)
                for leg, quantity in enumerate(legs):
                    if quantity == 0:
                        continue
                    intent_id = "paper-intent:"+digest({"decision_id": decision_id, "symbol": target.symbol, "leg": leg})
                    c.execute("INSERT INTO paper_intents VALUES(?,?,?,?,?,'0','pending')", (intent_id, decision_id, target.symbol, leg, str(quantity)))
            plan = c.execute("SELECT * FROM paper_plans WHERE decision_id=?", (decision_id,)).fetchone()
            self._report(c, plan, "accepted", "OK", now, "durable intents accepted; no fills implied")
            self.fault("before_stage_commit")
            return self.report(decision_id, connection=c)

    def _cancel(self, c, plan, now, reason, code="INVALID_INPUT"):
        if c.execute("SELECT 1 FROM paper_intents WHERE decision_id=? AND status='unknown'", (plan["decision_id"],)).fetchone():
            self._pause(c, "UNKNOWN_INTENT")
            return
        c.execute("UPDATE paper_intents SET status='cancelled' WHERE decision_id=? AND status IN ('pending','partial')", (plan["decision_id"],))
        filled = c.execute("SELECT 1 FROM paper_intents WHERE decision_id=? AND filled!='0'", (plan["decision_id"],)).fetchone()
        c.execute("UPDATE paper_plans SET status=?,reason=? WHERE decision_id=?", ("partial-cancelled" if filled else "failed", reason, plan["decision_id"]))
        self._report(c, plan, "partial" if filled else "failed", code, now, "CANCELLED: "+reason)
        self._pause(c, reason)

    @staticmethod
    def _trade(q, entry, signed, price):
        result = q+signed
        realized = Decimal(0)
        if q*signed >= 0:
            average = (abs(q)*entry+abs(signed)*price)/abs(result) if result else Decimal(0)
        else:
            realized = min(abs(q), abs(signed))*(price-entry)*(1 if q > 0 else -1)
            average = entry if result*q > 0 else price if result else Decimal(0)
        return result, average, realized

    def _fill(self, c, plan, intent, now):
        s = intent["symbol"]
        quote = json.loads(c.execute("SELECT payload FROM paper_quotes WHERE symbol=?", (s,)).fetchone()[0])
        mark, step = amount(quote["usd_per_unit"], positive=True), amount(quote["quantity_step"], positive=True)
        left = decimal_string(intent["quantity"])-decimal_string(intent["filled"])
        if left % step or (decimal_string(intent["filled"]) == 0 and abs(left)*mark < amount(self.config["execution"]["min_notional_usd"][s])):
            self._cancel(c, plan, now, "DUST_OR_MIN_NOTIONAL", "ORDER_RULE")
            return False
        quantity = (min(abs(left), amount(self.config["execution"]["max_fill_quantities"][s]))/step).to_integral_value(rounding=ROUND_DOWN)*step
        if quantity == 0:
            self._cancel(c, plan, now, "LIQUIDITY_BELOW_STEP", "ORDER_RULE")
            return False
        signed = quantity*(1 if left > 0 else -1)
        price = mark*(1+(1 if signed > 0 else -1)*amount(self.config["execution"]["slippage_bps"])/10000)
        if price <= 0 or not price.is_finite():
            self._cancel(c, plan, now, "INVALID_MODEL_PRICE", "ORDER_RULE")
            return False
        fee = quantity*price*amount(self.config["execution"]["fee_bps"])/10000
        fill = {"intent_id": intent["intent_id"], "symbol": s, "quantity": str(signed), "price": str(price), "fee": str(fee), "observed_ms": now}
        fill_id = "paper-fill:"+digest({"intent_id": intent["intent_id"], "previous_filled": intent["filled"]})
        c.execute("INSERT INTO paper_fills(fill_id,intent_id,payload,digest) VALUES(?,?,?,?)", (fill_id, intent["intent_id"], canonical(fill), digest(fill)))
        row = c.execute("SELECT * FROM paper_positions WHERE symbol=?", (s,)).fetchone()
        q, entry = (decimal_string(row["quantity"]), decimal_string(row["entry_price"])) if row else (Decimal(0), Decimal(0))
        new, average, realized = self._trade(q, entry, signed, price)
        c.execute("INSERT INTO paper_positions VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET quantity=excluded.quantity,entry_price=excluded.entry_price",
                  (s, str(new), str(average)))
        meta = self._meta(c)
        meta["cash"] = str(decimal_string(meta["cash"])+realized-fee)
        self._save_meta(c, meta)
        filled = decimal_string(intent["filled"])+signed
        c.execute("UPDATE paper_intents SET filled=?,status=? WHERE intent_id=?",
                  (str(filled), "filled" if filled == decimal_string(intent["quantity"]) else "partial", intent["intent_id"]))
        return True

    @stable_decimal
    def advance(self):
        did_fill = False
        with self.store.transaction() as (c, now):
            plan = c.execute("SELECT * FROM paper_plans WHERE status IN ('running','unknown') ORDER BY created_ms,decision_id LIMIT 1").fetchone()
            self._supervise(c, now)
            if plan is None:
                return False
            self._fence(c, plan)
            proposal = self._proposal(plan)
            decision = Decision.from_dict(proposal["decision"])
            job = JobQueue._job(c, c.execute("SELECT job_id FROM processing WHERE processing_id=?", (plan["processing_id"],)).fetchone()[0])[1]
            if plan["status"] == "unknown":
                self._pause(c, "UNKNOWN_INTENT")
                return False
            from ..contracts import ForecastBatch
            invalid = Outbox._invalid(c, job, ForecastBatch.from_dict(proposal["forecast"]), now)
            if invalid or decision.expires_ms <= now or now >= plan["created_ms"]+self.config["execution"]["order_timeout_ms"]:
                self._cancel(c, plan, now, invalid or "EXPIRED", "EXPIRED" if not invalid or invalid == "EXPIRED" else "INVALID_INPUT")
                return True
            if self._meta(c)["paused"]:
                return False
            state = self._state(c, now, job, exclude_decision=decision.decision_id)
            approval = SimulationRiskGate(self.policy, self._supervisor(c)).recheck_execution(proposal, state, now)
            c.execute("INSERT INTO paper_checks(decision_id,observed_ms,payload) VALUES(?,?,?)",
                      (decision.decision_id, now, canonical({"approval": approval, "state": state, "phase": "before-fill"})))
            if approval["verdict"]["status"] != "approved":
                self._cancel(c, plan, now, "RISK_RECHECK_FAILED")
                return True
            intent = c.execute("SELECT i.* FROM paper_intents i WHERE i.decision_id=? AND i.status IN ('pending','partial') "
                               "AND NOT EXISTS(SELECT 1 FROM paper_intents p WHERE p.decision_id=i.decision_id AND p.symbol=i.symbol "
                               "AND p.leg<i.leg AND p.status!='filled') ORDER BY i.symbol,i.leg LIMIT 1", (decision.decision_id,)).fetchone()
            if intent:
                c.execute("SAVEPOINT projected_fill")
                did_fill = self._fill(c, plan, intent, now)
                if did_fill:
                    projected = self._state(c, now, job, exclude_decision=decision.decision_id)
                    try:
                        targets = {t.symbol: t.quantity for t in decision.targets}
                        bad = bool(exposure_metrics(projected, self.policy, now, targets)["breaches"])
                    except (ValueError, KeyError, TypeError, ArithmeticError):
                        bad = True
                    if bad:
                        c.execute("ROLLBACK TO projected_fill")
                        did_fill = False
                        self._cancel(c, plan, now, "PROJECTED_COST_RISK", "RISK_LIMIT")
                    else:
                        remaining = c.execute("SELECT 1 FROM paper_intents WHERE decision_id=? AND status!='filled' LIMIT 1", (decision.decision_id,)).fetchone()
                        c.execute("UPDATE paper_plans SET status=? WHERE decision_id=?", ("running" if remaining else "completed", decision.decision_id))
                        self._supervise(c, now)
                        self._report(c, plan, "partial" if remaining else "completed", "OK", now, "partial fills" if remaining else "exact targets confirmed")
                        self.fault("before_fill_commit")
                c.execute("RELEASE projected_fill")
            else:
                c.execute("UPDATE paper_plans SET status='completed' WHERE decision_id=?", (decision.decision_id,))
                self._report(c, plan, "completed", "OK", now, "no quantity change; exact targets confirmed")
        if did_fill:
            self.fault("after_fill_commit")
        return True

    @stable_decimal
    def step(self):
        with self.store.connection() as c:
            active = c.execute("SELECT 1 FROM paper_plans WHERE status IN ('running','unknown') LIMIT 1").fetchone()
        if active:
            return self.advance()
        if self.status()["paused"]:
            return False
        delivery = self.outbox.begin()
        if delivery is None:
            return False
        try:
            self.stage(delivery)
        except (ValueError, KeyError, TypeError, ArithmeticError):
            self.outbox.finish(delivery.processing.processing_id, delivery.delivery_epoch, "block", reason_code="DATA_UNAVAILABLE")
        return True

    def report(self, decision_id, *, connection=None):
        if connection is None:
            with self.store.connection() as c:
                return self.report(decision_id, connection=c)
        row = connection.execute("SELECT report FROM paper_plans WHERE decision_id=?", (decision_id,)).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return report_from_dict(json.loads(row[0]))

    @stable_decimal
    def verify_ledger(self):
        with self.store.connection() as c:
            c.execute("BEGIN")
            self._verify(c)

    def _verify(self, c):
        cash = decimal_string(self.config["initial_state"]["account"]["equity_usd"])
        book = {p["symbol"]: (decimal_string(p["quantity"]), decimal_string(p["mark_price"])) for p in self.config["initial_state"]["account"]["positions"]}
        totals = {}
        for row in c.execute("SELECT * FROM paper_fills ORDER BY sequence"):
            fill = json.loads(row["payload"])
            if digest(fill) != row["digest"] or fill["intent_id"] != row["intent_id"]:
                raise ValueError("Paper fill evidence corrupt")
            signed, price, fee = decimal_string(fill["quantity"]), amount(fill["price"], positive=True), amount(fill["fee"])
            intent = c.execute("SELECT * FROM paper_intents WHERE intent_id=?", (row["intent_id"],)).fetchone()
            previous = totals.get(row["intent_id"], Decimal(0))
            expected_id = "paper-fill:"+digest({"intent_id": row["intent_id"], "previous_filled": str(previous)})
            if (intent is None or intent["symbol"] != fill["symbol"] or signed*decimal_string(intent["quantity"]) <= 0
                    or row["fill_id"] != expected_id or fee != abs(signed)*price*amount(self.config["execution"]["fee_bps"])/10000):
                raise ValueError("Paper fill identity/side/fee mismatch")
            q, entry = book.get(fill["symbol"], (Decimal(0), Decimal(0)))
            new, average, realized = self._trade(q, entry, signed, price)
            book[fill["symbol"]] = new, average
            cash += realized-fee
            totals[row["intent_id"]] = totals.get(row["intent_id"], Decimal(0))+signed
        actual = {r["symbol"]: (decimal_string(r["quantity"]), decimal_string(r["entry_price"])) for r in c.execute("SELECT * FROM paper_positions")}
        if actual != book or decimal_string(self._meta(c)["cash"]) != cash:
            raise ValueError("Paper book does not reconcile to immutable fills")
        for plan in c.execute("SELECT * FROM paper_plans"):
            proposal = self._proposal(plan)
            original = json.loads(plan["proposal"])
            initial = {p["symbol"]: decimal_string(p["quantity"]) for p in original["paper_initial_positions"]}
            expected = {}
            for target in Decision.from_dict(proposal["decision"]).targets:
                q = initial.get(target.symbol, Decimal(0))
                legs = (-q, target.quantity) if q*target.quantity < 0 else (target.quantity-q,)
                for leg, quantity in enumerate(legs):
                    if quantity:
                        key = "paper-intent:"+digest({"decision_id": plan["decision_id"], "symbol": target.symbol, "leg": leg})
                        expected[key] = (target.symbol, leg, quantity)
            actual_intents = {r["intent_id"]: (r["symbol"], r["leg"], decimal_string(r["quantity"]))
                              for r in c.execute("SELECT * FROM paper_intents WHERE decision_id=?", (plan["decision_id"],))}
            if expected != actual_intents:
                raise ValueError("Paper intents differ from immutable target plan")
        for row in c.execute("SELECT * FROM paper_intents"):
            filled, intended = decimal_string(row["filled"]), decimal_string(row["quantity"])
            if totals.get(row["intent_id"], Decimal(0)) != filled or filled*intended < 0 or abs(filled) > abs(intended):
                raise ValueError("Intent cumulative fill mismatch")

    @stable_decimal
    def mark_unknown(self, decision_id):
        """Explicit local fault simulation; no external order is sent or inferred."""
        with self.store.transaction() as (c, now):
            plan = c.execute("SELECT * FROM paper_plans WHERE decision_id=? AND status='running'", (decision_id,)).fetchone()
            if not plan:
                raise ValueError("Only an active Paper decision can become unknown")
            c.execute("UPDATE paper_intents SET status='unknown' WHERE decision_id=? AND status IN ('pending','partial')", (decision_id,))
            c.execute("UPDATE paper_plans SET status='unknown' WHERE decision_id=?", (decision_id,))
            self._pause(c, "UNKNOWN_INTENT")
            self._report(c, plan, "unknown", "NETWORK_UNKNOWN", now, "local uncertainty injection; reconcile ledger before resume")

    @stable_decimal
    def reconcile(self, request_id, actor):
        identity(actor)
        with self.store.transaction() as (c, now):
            def apply():
                self._verify(c)
                for row in c.execute("SELECT * FROM paper_intents WHERE status='unknown'").fetchall():
                    filled = decimal_string(row["filled"])
                    status = "filled" if filled == decimal_string(row["quantity"]) else "partial" if filled else "pending"
                    c.execute("UPDATE paper_intents SET status=? WHERE intent_id=?", (status, row["intent_id"]))
                c.execute("UPDATE paper_plans SET status='running' WHERE status='unknown'")
                self._pause(c, "RECONCILED_REQUIRES_RESUME")
                self.store.event(c, now, "paper-reconciled", {"actor": actor, "request_id": request_id})
                return {"ledger_verified": True, "paused": True, "real_execution_authorized": False}
            return self._operator(c, request_id, {"action": "reconcile", "actor": actor}, apply)

    @stable_decimal
    def cancel(self, decision_id, request_id, actor):
        identity(actor)
        with self.store.transaction() as (c, now):
            def apply():
                plan = c.execute("SELECT * FROM paper_plans WHERE decision_id=?", (decision_id,)).fetchone()
                if plan is None:
                    raise KeyError(decision_id)
                if plan["status"] not in TERMINAL:
                    self._cancel(c, plan, now, "OPERATOR_CANCEL")
                return {"report": self.report(decision_id, connection=c).to_dict()}
            return self._operator(c, request_id, {"action": "cancel", "actor": actor, "decision_id": decision_id}, apply)

    @stable_decimal
    def status(self):
        with self.store.transaction() as (c, now):
            report = self._supervise(c, now)
            return {"mode": "synthetic-paper", **self._meta(c), "supervision": report,
                    "open_intents": c.execute("SELECT count(*) FROM paper_intents WHERE status IN ('pending','partial','unknown')").fetchone()[0],
                    "executions": [{"decision_id": r["decision_id"], "status": r["status"],
                                    "report_id": json.loads(r["report"])["report_id"]}
                                   for r in c.execute("SELECT decision_id,status,report FROM paper_plans ORDER BY created_ms DESC LIMIT 20")],
                    "real_execution_authorized": False}

    def _operator(self, c, request_id, payload, action):
        identity(request_id)
        old = c.execute("SELECT * FROM paper_operator_requests WHERE request_id=?", (request_id,)).fetchone()
        if old:
            if old["payload"] != canonical(payload):
                raise ValueError("Operator request ID conflict")
            return json.loads(old["response"])
        response = action()
        c.execute("INSERT INTO paper_operator_requests VALUES(?,?,?)", (request_id, canonical(payload), canonical(response)))
        return response

    @stable_decimal
    def resume(self, request_id, generation, pause_epoch, actor):
        identity(actor, generation)
        from .contracts import decode
        decode(int, pause_epoch)
        with self.store.transaction() as (c, now):
            def apply():
                m = self._meta(c)
                if m["generation"] != generation or m["pause_epoch"] != pause_epoch or generation == "not-booted":
                    raise ValueError("Stale resume authorization; inspect current boot/pause epoch")
                self._supervisor(c).resume(self._state(c, now), now)
                m.update(paused=False, reasons=[])
                self._save_meta(c, m)
                self.store.event(c, now, "paper-operator-resume", {"actor": actor, "request_id": request_id, "generation": generation})
                return {"resumed": True, "generation": generation, "pause_epoch": pause_epoch}
            return self._operator(c, request_id, {"action": "resume", "generation": generation, "pause_epoch": pause_epoch, "actor": actor}, apply)

    def _report(self, c, plan, status, code, now, reason):
        proposal = json.loads(plan["proposal"])
        decision = Decision.from_dict(proposal["decision"])
        actual = {r["symbol"]: decimal_string(r["quantity"]) for r in c.execute("SELECT * FROM paper_positions")}
        initial = {p["symbol"]: decimal_string(p["quantity"]) for p in proposal["paper_initial_positions"]}
        items = []
        for target in decision.targets:
            ids = tuple(r[0] for r in c.execute("SELECT intent_id FROM paper_intents WHERE decision_id=? AND symbol=? ORDER BY leg",
                                              (decision.decision_id, target.symbol)))
            q = actual.get(target.symbol, Decimal(0))
            items.append(ExecutionItem(target.symbol, target.quantity, None if status == "unknown" else q,
                                       q-initial.get(target.symbol, Decimal(0)), ids,
                                       reason if status in {"partial", "failed", "unknown", "rejected"} else None))
        sequence = plan["sequence"]+1
        report = ExecutionReport(decision.decision_id+":"+str(sequence), decision.decision_id, sequence, now,
                                 status, code, tuple(items), "SYNTHETIC PAPER ONLY: "+reason)
        c.execute("INSERT INTO paper_reports VALUES(?,?,?)", (decision.decision_id, sequence, canonical(report.to_dict())))
        c.execute("UPDATE paper_plans SET report=?,sequence=? WHERE decision_id=?", (canonical(report.to_dict()), sequence, decision.decision_id))
        record = ProcessingRecord.from_dict(json.loads(c.execute("SELECT record FROM processing WHERE processing_id=?", (plan["processing_id"],)).fetchone()[0]))
        checked = c.execute("SELECT payload FROM paper_checks WHERE decision_id=? ORDER BY sequence DESC LIMIT 1", (decision.decision_id,)).fetchone()
        verdict_id = json.loads(checked[0])["approval"]["verdict"]["verdict_id"] if checked else None
        record = replace(record, decision_id=decision.decision_id, report_id=report.report_id, verdict_id=verdict_id)
        if status in {"completed", "failed", "rejected"} or reason.startswith("CANCELLED"):
            event = "complete" if status == "completed" else "block"
            record = transition_processing(record, event, now, reason_code=code)
            if event == "complete":
                job = JobQueue._job(c, record.job_id)[1]
                c.execute("UPDATE controls SET consumed_asof_ms=? WHERE scope=?", (job.asof_ms, job.control_scope))
            Outbox._write(c, record, "done", plan["delivery_epoch"])
        else:
            Outbox._write(c, record, "running", plan["delivery_epoch"])
        self.store.event(c, now, "paper-report", {"decision_id": decision.decision_id, "report_id": report.report_id, "status": status})
        return report
