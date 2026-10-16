"""PR0 contract tests: no server, database, wall-clock sleeps or exchange clients."""
import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from perpetual_alpha.runtime.contracts import (
    AnalysisJob,
    JobLimits,
    LeaseRef,
    ProcessingRecord,
    transition_job,
    transition_processing,
)
from perpetual_alpha.transport.messages import (
    ClaimRequest,
    ClaimResponse,
    DataQueryRequest,
    DataQueryResponse,
    FeedbackResponse,
    ForecastSubmission,
    parse_message,
)
from perpetual_alpha.transport.schemas import MESSAGES, bundle

ROOT = Path(__file__).resolve().parents[1]


class TransportContractTests(unittest.TestCase):
    def setUp(self):
        self.examples = json.loads((ROOT / "docs/examples/agent-transport.json").read_text(encoding="utf-8"))
        self.claim = ClaimResponse.from_dict(self.examples["ClaimResponse"])

    def test_all_request_response_examples_round_trip(self):
        self.assertEqual(set(self.examples), {c.__name__ for c in MESSAGES})
        for cls in MESSAGES:
            with self.subTest(cls=cls.__name__):
                payload = self.examples[cls.__name__]
                obj = parse_message(cls, json.dumps(payload).encode(), max_bytes=65536)
                self.assertEqual(obj.to_dict(), cls.from_dict(obj.to_dict()).to_dict())

    def test_every_message_rejects_missing_unknown_fields_and_version(self):
        for cls in MESSAGES:
            for change in ({"extra": True}, {"transport_version": "future/9"}):
                with self.subTest(cls=cls.__name__, change=change), self.assertRaises(ValueError):
                    cls.from_dict({**self.examples[cls.__name__], **change})
            payload = dict(self.examples[cls.__name__])
            payload.pop("transport_version")
            with self.assertRaises(ValueError):
                cls.from_dict(payload)

    def test_bounded_json_rejects_duplicate_keys_nan_arrays_and_invalid_encoding(self):
        cases = [b'{"request_id":"a","request_id":"b","transport_version":"agent-transport/0.1"}',
                 b'{"request_id":NaN}', b'{"request_id":Infinity}', b'[]', b'\xff', b'{']
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_message(ClaimRequest, raw, max_bytes=1024)
        with self.assertRaises(ValueError):
            parse_message(ClaimRequest, b'{}', max_bytes=1)

    def test_strict_nested_types_no_bool_as_integer(self):
        for change in ({"attempt": True}, {"deadline_ms": "220000"}, {"control_epoch": 0},
                       {"symbols": "par:BTC"}, {"symbols": ["par:BTC", "par:BTC"]},
                       {"symbols": ["BTC"]}, {"created_ms": 220000}, {"attempt": 3},
                       {"status": "submitted", "attempt": 0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                AnalysisJob.from_dict({**self.claim.job.to_dict(), **change})
        for bad in (0, -1, True):
            with self.assertRaises(ValueError):
                replace(self.claim.job.limits, max_attempts=bad)
        with self.assertRaises(ValueError):
            replace(self.claim.job.limits, max_bytes=2**53)

    def test_invalid_nested_lease_and_claim_binding(self):
        for change in ({"job_id": "other"}, {"lease_epoch": 2}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.claim, lease=replace(self.claim.lease, ref=replace(self.claim.lease.ref, **change)))
        with self.assertRaises(ValueError):
            replace(self.claim, lease=replace(self.claim.lease, expires_ms=220001))
        with self.assertRaises(ValueError):
            LeaseRef("j", 0, 0, "t")

    def test_data_request_shape_and_parameters_are_preserved(self):
        payload = copy.deepcopy(self.examples["DataQueryRequest"])
        payload["data_request"].update(kind="candle", parameters={"interval": "1m"})
        parsed = DataQueryRequest.from_dict(payload)
        self.assertEqual(parsed.data_request.parameters, {"interval": "1m"})
        for change in ({"fields": []}, {"symbols": "par:BTC"}, {"max_records": True},
                       {"symbols": ["BTC"]}, {"fields": ["bid", "bid"]}, {"extra": 1}):
            bad = copy.deepcopy(payload)
            bad["data_request"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                DataQueryRequest.from_dict(bad)

    def test_forecast_optional_numeric_fields_and_business_version_are_unchanged(self):
        payload = copy.deepcopy(self.examples["ForecastSubmission"])
        item = payload["forecast_batch"]["items"][0]
        for name in list(item):
            if name not in {"symbol", "asof_ms", "expires_ms", "horizon_ms", "status", "direction", "evidence_refs"}:
                del item[name]
        batch = ForecastSubmission.from_dict(payload).forecast_batch
        self.assertIsNone(batch.items[0].expected_return)
        self.assertEqual(batch.schema_version, "forecast/0.1")
        payload["forecast_batch"]["schema_version"] = "forecast/99"
        with self.assertRaises(ValueError):
            ForecastSubmission.from_dict(payload)

    def test_feedback_cannot_claim_processing_or_receipt_for_unsubmitted_job(self):
        feedback = FeedbackResponse.from_dict(self.examples["FeedbackResponse"])
        for change in ({"job_id": "wrong"}, {"job_status": "running"}, {"receipt": None},
                       {"processing": replace(feedback.processing, processing_id="other")}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(feedback, **change)

    def test_data_response_quality_and_frozen_cutoff_are_validated(self):
        response = DataQueryResponse.from_dict(self.examples["DataQueryResponse"])
        for change in ({"asof_ms": 99000}, {"records": ()}, {"error": "bad"},
                       {"payload_digest": "invalid"}, {"evidence_ref": "wrong"},
                       {"status": "missing"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(response, **change)
        missing = replace(response, status="missing", records=(), error="MISSING", evidence_ref=None)
        self.assertEqual(missing.status, "missing")

    def test_nested_json_duplicate_keys_are_rejected(self):
        raw = json.dumps(self.examples["ForecastSubmission"]).replace(
            '"direction": "up"', '"direction": "down", "direction": "up"').encode()
        with self.assertRaises(ValueError):
            parse_message(ForecastSubmission, raw, max_bytes=65536)

    def test_exported_bundle_matches_generator_and_separates_legacy_schemas(self):
        saved = json.loads((ROOT / "docs/schemas/AgentTransport.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, bundle())
        self.assertEqual(len(MESSAGES), 13)


class StateRuleTests(unittest.TestCase):
    def setUp(self):
        self.queued = AnalysisJob("j", "c", "p", "v", "scope", 1, "control", ("par:BTC",),
                                  100, 100, 1000, "queued", 0, 0, JobLimits(10, 100, 1024, 2, 900, 100))
        self.running = transition_job(self.queued, "claim", 101)

    def test_claim_retry_fences_old_attempt_and_exhaustion_fails(self):
        queued = transition_job(self.running, "lease_lost", 200)
        new = transition_job(queued, "claim", 201)
        self.assertEqual((new.attempt, new.lease_epoch), (2, 2))
        self.assertEqual(transition_job(new, "lease_lost", 300).status, "failed")
        self.assertEqual(self.queued.attempt, 0)

    def test_all_analysis_terminal_states_cannot_requeue_or_submit_again(self):
        for event in ("submit", "no_forecast", "fail"):
            terminal = transition_job(self.running, event, 200)
            for next_event in ("claim", "submit", "no_forecast", "lease_lost", "expire"):
                with self.subTest(event=event, next=next_event), self.assertRaises(ValueError):
                    transition_job(terminal, next_event, 300)

    def test_exact_deadline_expires_queued_or_running_but_not_submitted(self):
        for job in (self.queued, self.running):
            self.assertEqual(transition_job(job, "expire", 1000).status, "expired")
            with self.assertRaises(ValueError):
                transition_job(job, "submit", 1000)
        submitted = transition_job(self.running, "submit", 999)
        with self.assertRaises(ValueError):
            transition_job(submitted, "expire", 1000)

    def test_illegal_state_time_and_predeadline_expiry_rejected(self):
        for event, now in (("submit", 101), ("expire", 999), ("claim", 99), ("claim", True)):
            with self.subTest(event=event, now=now), self.assertRaises(ValueError):
                transition_job(self.queued, event, now)

    def test_processing_recovery_preserves_identity_without_touching_analysis(self):
        submitted = transition_job(self.running, "submit", 200)
        pending = ProcessingRecord("proc", "j", "pending", "OK", 200, 1, None, None, None)
        running = transition_processing(pending, "start", 201)
        recovered = transition_processing(running, "recover", 202)
        self.assertEqual((recovered.processing_id, recovered.status, recovered.revision), ("proc", "pending", 3))
        self.assertEqual(submitted.status, "submitted")
        for event in ("complete", "block", "fail"):
            terminal = transition_processing(running, event, 202, reason_code="OK" if event == "complete" else "EXPIRED")
            with self.assertRaises(ValueError):
                transition_processing(terminal, "recover", 203)
        with self.assertRaises(ValueError):
            transition_processing(running, "complete", 200)
