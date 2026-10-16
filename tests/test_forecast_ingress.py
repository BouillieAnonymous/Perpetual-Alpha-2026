"""New ingress shares Forecast semantics while enforcing trusted job/lease/manifest scope."""
import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from perpetual_alpha.contracts import ForecastBatch
from perpetual_alpha.forecasting import ForecastUnavailable, checked_forecast
from perpetual_alpha.interfaces import DataRequest
from perpetual_alpha.runtime.ingress import (
    IngressRejected,
    Principal,
    input_digest,
    validate_ingress,
    validate_lease,
)
from perpetual_alpha.transport.messages import ClaimResponse, ForecastSubmission


class IngressTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        examples = json.loads((root / "docs/examples/agent-transport.json").read_text(encoding="utf-8"))
        self.submission = ForecastSubmission.from_dict(examples["ForecastSubmission"])
        claim = ClaimResponse.from_dict(examples["ClaimResponse"])
        self.job, self.lease = claim.job, claim.lease
        self.principal = Principal("dummy-worker", "dummy-agent", "test-0.1", "control")
        self.requests = (DataRequest("btc-quotes", "hyperliquid-testnet/par", "quote",
                                     ("par:BTC",), ("bid", "ask"), 0, 1, 90000),)
        self.context = {"protocol_version": "1.0", "context_id": "ctx-001", "asof_ms": 100000,
                        "account": None, "execution_reports": [], "datasets": {"btc-quotes": {
                            "source": "hyperliquid-testnet/par", "kind": "quote", "parameters": {},
                            "required": True, "status": "ready", "records": [{"symbol": "par:BTC",
                            "event_ms": 99000, "received_ms": 99500, "data": {"bid": "99", "ask": "101"}}]}}}
        self.manifest = input_digest(self.context, self.requests)

    def validate(self, submission=None, **kwargs):
        params = {"job": self.job, "lease": self.lease, "principal": self.principal,
                  "context": self.context, "requests": self.requests, "manifest_digest": self.manifest,
                  "received_ms": 108001, "current_control_epoch": 1}
        params.update(kwargs)
        return validate_ingress(submission or self.submission, **params)

    def mutate_item(self, **changes):
        batch = self.submission.forecast_batch
        return replace(self.submission, forecast_batch=replace(batch, items=(replace(batch.items[0], **changes),)))

    def test_valid_result_is_preflight_only_no_io_or_receipt(self):
        with patch("sqlite3.connect", side_effect=AssertionError("No persistence in PR0")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("No HTTP in PR0")), \
                patch("perpetual_alpha.execution.Exchange", side_effect=AssertionError("No signing")):
            result = self.validate()
        self.assertEqual(result.initial_processing_status, "pending")
        self.assertEqual(result.manifest_digest, self.manifest)
        self.assertFalse(hasattr(result, "receipt"))

    def test_shared_sync_and_remote_result_validation_agree(self):
        batch = self.submission.forecast_batch
        requests = self.requests

        class Producer:
            producer_id, producer_version = batch.producer_id, batch.producer_version

            def requirements(self):
                return requests

            def forecast(self, context):
                return batch

        sync = checked_forecast(Producer(), self.context, clock=lambda: 108001,
                                max_inference_ms=20000, max_input_age_ms=90000)
        self.assertEqual(sync.to_dict(), self.validate().batch.to_dict())

    def test_same_forecast_new_submission_id_or_lease_has_same_payload_digest(self):
        first = self.validate()
        other = self.validate(replace(self.submission, submission_id="different"))
        self.assertEqual(first.payload_digest, other.payload_digest)
        changed = self.validate(self.mutate_item(direction="down"))
        self.assertNotEqual(first.payload_digest, changed.payload_digest)

    def test_minimal_wire_payload_normalizes_optional_values_for_digest(self):
        raw = self.submission.to_dict()
        item = raw["forecast_batch"]["items"][0]
        for key in list(item):
            if item[key] is None:
                del item[key]
        self.assertEqual(self.validate(ForecastSubmission.from_dict(raw)).payload_digest,
                         self.validate().payload_digest)

    def test_identity_role_and_worker_cannot_be_self_asserted(self):
        for changes in ({"producer_id": "other"}, {"producer_version": "other"},
                        {"worker_id": "other"}, {"role": "shadow"}):
            with self.subTest(changes=changes), self.assertRaises(IngressRejected) as error:
                self.validate(principal=replace(self.principal, **changes))
            self.assertEqual(error.exception.code, "FORBIDDEN")

    def test_lost_lease_cross_job_attempt_epoch_and_token_rejected(self):
        for changes in ({"job_id": "other"}, {"attempt": 2, "lease_epoch": 2},
                        {"lease_epoch": 2}, {"lease_token": "wrong"}):
            ref = replace(self.submission.lease, **changes)
            with self.subTest(changes=changes), self.assertRaises(IngressRejected) as error:
                self.validate(replace(self.submission, lease=ref))
            self.assertEqual(error.exception.code, "LEASE_LOST")

    def test_expiry_and_clock_boundaries_are_server_owned(self):
        for now, code in ((99999, "INVALID_REQUEST"), (130000, "LEASE_LOST"), (220000, "EXPIRED")):
            with self.subTest(now=now), self.assertRaises(IngressRejected) as error:
                self.validate(received_ms=now)
            self.assertEqual(error.exception.code, code)
        with self.assertRaises(IngressRejected):
            self.validate(lease=replace(self.lease, expires_ms=220001))
        with self.assertRaises(ForecastUnavailable) as error:
            self.validate(received_ms=120001)
        self.assertEqual(error.exception.reason_code, "INFERENCE_TIMEOUT")
        self.validate(received_ms=120000)

    def test_common_lease_guard_covers_heartbeat_query_and_complete(self):
        validate_lease(self.job, self.lease, self.submission.lease, self.principal, 108001)
        with self.assertRaises(IngressRejected):
            validate_lease(replace(self.job, status="submitted"), self.lease,
                           self.submission.lease, self.principal, 108001)

    def test_accepted_newer_equal_asof_and_switched_control_epoch_block_old_results(self):
        for params in ({"accepted_asof_ms": 100000}, {"accepted_asof_ms": 100001},
                       {"current_control_epoch": 2}):
            with self.subTest(params=params), self.assertRaises(IngressRejected) as error:
                self.validate(**params)
            self.assertEqual(error.exception.code, "STALE_RESULT")
        self.validate(accepted_asof_ms=99999)

    def test_no_new_submission_after_any_analysis_terminal(self):
        for status in ("submitted", "completed_without_forecast", "failed", "expired", "queued"):
            with self.subTest(status=status), self.assertRaises(IngressRejected):
                self.validate(job=replace(self.job, status=status))

    def test_context_changed_or_cross_job_manifest_rejected(self):
        for changes in ({"context_id": "other"}, {"asof_ms": 100001}, {"account": {"observed_ms": 99999}}):
            context = {**copy.deepcopy(self.context), **changes}
            with self.subTest(changes=changes), self.assertRaises(IngressRejected):
                self.validate(context=context)
        context = copy.deepcopy(self.context)
        context["datasets"]["btc-quotes"]["records"][0]["data"]["bid"] = "90"
        with self.assertRaises(IngressRejected):
            self.validate(context=context)

    def test_shared_context_checks_missing_required_wrong_source_future_record_and_fields(self):
        for change in ("missing", "source", "future", "field"):
            context = copy.deepcopy(self.context)
            dataset = context["datasets"]["btc-quotes"]
            if change == "missing":
                dataset.update(status="missing", error="MISSING")
            elif change == "source":
                dataset["source"] = "other"
            elif change == "future":
                dataset["records"][0]["received_ms"] = 100001
            else:
                dataset["records"][0]["data"].pop("ask")
            with self.subTest(change=change), self.assertRaises(ForecastUnavailable):
                self.validate(context=context, manifest_digest=input_digest(context, self.requests))

    def test_unknown_or_unavailable_evidence_and_forecast_asset_rejected(self):
        for changes in ({"evidence_refs": ("other",)}, {"symbol": "par:GOLD"}, {"asof_ms": 99000},
                        {"expires_ms": 108001}):
            with self.subTest(changes=changes), self.assertRaises(ForecastUnavailable):
                self.validate(self.mutate_item(**changes))

    def test_auxiliary_data_access_does_not_grant_forecast_permission(self):
        self.requests = (replace(self.requests[0], symbols=("par:BTC", "par:GOLD")),)
        self.manifest = input_digest(self.context, self.requests)
        with self.assertRaises(ForecastUnavailable) as error:
            self.validate(self.mutate_item(symbol="par:GOLD"))
        self.assertEqual(error.exception.reason_code, "UNDECLARED_ASSET")

    def test_error_batch_is_preserved_but_entire_processing_is_blocked(self):
        error_item = replace(self.submission.forecast_batch.items[0], symbol="par:ETH",
                             status="error", direction=None, evidence_refs=())
        batch = replace(self.submission.forecast_batch, items=(*self.submission.forecast_batch.items, error_item))
        job = replace(self.job, symbols=("par:BTC", "par:ETH"))
        result = self.validate(replace(self.submission, forecast_batch=batch), job=job)
        self.assertEqual(result.initial_processing_status, "blocked")
        self.assertEqual(result.reason_code, "PRODUCER_ERROR")
        self.assertEqual(len(result.batch.items), 2)

    def test_neutral_abstain_empty_batch_no_new_implicit_exit_policy(self):
        for status, direction in (("ready", "neutral"), ("abstain", None)):
            result = self.validate(self.mutate_item(status=status, direction=direction, evidence_refs=()))
            self.assertEqual(result.initial_processing_status, "pending")
            self.assertFalse(hasattr(result, "targets"))
        batch = replace(self.submission.forecast_batch, items=())
        self.assertEqual(self.validate(replace(self.submission, forecast_batch=batch)).batch.items, ())

    def test_shadow_never_queues_portfolio_even_with_valid_forecast(self):
        result = self.validate(job=replace(self.job, role="shadow"),
                               principal=replace(self.principal, role="shadow"), accepted_asof_ms=100001)
        self.assertEqual((result.initial_processing_status, result.reason_code), ("completed", "SHADOW_ONLY"))

    def test_batch_time_context_version_and_identity_revalidated(self):
        for changes in ({"created_ms": 108002}, {"created_ms": 99999},
                        {"context_id": "other"}, {"producer_version": "other"}):
            with self.subTest(changes=changes), self.assertRaises((ForecastUnavailable, IngressRejected)):
                batch = self.submission.forecast_batch
                if changes.get("created_ms") == 99999:
                    batch = replace(batch, items=(replace(batch.items[0], asof_ms=99000),))
                batch = replace(batch, **changes)
                self.validate(replace(self.submission, forecast_batch=batch))

    def test_mutation_inside_frozen_forecast_object_is_not_trusted(self):
        batch = ForecastBatch.from_dict(self.submission.forecast_batch.to_dict())
        object.__setattr__(batch.items[0], "direction", "not-valid")
        object.__setattr__(self.submission, "forecast_batch", batch)
        with self.assertRaises(IngressRejected):
            self.validate()

    def test_stale_input_budget_is_independent_of_lease_and_inference(self):
        job = replace(self.job, limits=replace(self.job.limits, max_input_age_ms=8000))
        with self.assertRaises(ForecastUnavailable) as error:
            self.validate(job=job)
        self.assertEqual(error.exception.reason_code, "STALE_INPUT")

    def test_forecast_creation_cannot_predate_the_current_attempt_claim(self):
        lease = replace(self.lease, claimed_ms=109000)
        with self.assertRaises(ForecastUnavailable) as error:
            self.validate(lease=lease, received_ms=110000)
        self.assertEqual(error.exception.reason_code, "INVALID_TIME")
