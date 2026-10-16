"""Exact predeclared synthetic quote view only. Dynamic/historical gateway remains PR4."""
import json
from dataclasses import asdict

from ..journal import canonical, digest
from ..runtime.contracts import Lease
from ..runtime.ingress import validate_lease
from ..runtime.jobs import reject
from .messages import DataQueryRequest, DataQueryResponse, DataRecord


def query_fixture(queue, principal, message):
    message = DataQueryRequest.from_dict(message.to_dict())
    request = message.data_request
    fingerprint = digest(asdict(request))
    with queue.store.transaction() as (c, now):
        principal = queue._auth(c, principal)
        row, job = queue._owned(c, message.lease.job_id, principal)
        if row["lease"] is None:
            reject("LEASE_LOST", "No active analysis lease")
        validate_lease(job, Lease.from_dict(json.loads(row["lease"])), message.lease, principal, now)
        queue._control(c, job)
        if c.execute("SELECT 1 FROM gateway_reads WHERE job_id=? AND attempt=? AND request_id=?",
                     (job.job_id, job.attempt, message.request_id)).fetchone():
            reject("ID_CONFLICT", "Request ID already belongs to a historical query")
        old = c.execute("SELECT * FROM fixture_queries WHERE job_id=? AND attempt=? AND request_id=?",
                        (job.job_id, job.attempt, message.request_id)).fetchone()
        if old:
            if old["request_digest"] != fingerprint:
                reject("ID_CONFLICT", "Query ID content conflict")
            return DataQueryResponse.from_dict(json.loads(old["response"]))
        manifest = json.loads(row["manifest"])
        declared = next((r for r in manifest["requests"] if r["dataset_id"] == request.dataset_id), None)
        if declared is None or canonical(declared) != canonical(asdict(request)) or request.source != "fixture/par":
            reject("FORBIDDEN", "Only the exact predeclared fixture query is permitted")
        usage = c.execute("SELECT count(*),coalesce(sum(response_bytes),0) FROM fixture_queries "
                          "WHERE job_id=?", (job.job_id,)).fetchone()
        historical = c.execute("SELECT count(*),coalesce(sum(response_bytes),0) FROM gateway_datasets "
                               "WHERE job_id=?", (job.job_id,)).fetchone()
        dataset = manifest["context"]["datasets"][request.dataset_id]
        if usage[0] + historical[0] >= job.limits.max_queries or len(dataset["records"]) > job.limits.max_records_per_query:
            reject("LIMIT_EXCEEDED", "Fixture query count/records budget reached")
        response = DataQueryResponse(message.request_id, request.dataset_id, request.source, request.kind,
                                     "fixture-quote/1.0", job.asof_ms, dataset["status"], dataset["units"],
                                     tuple(DataRecord.from_dict(r) for r in dataset["records"]),
                                     dataset["truncated"], dataset["error"],
                                     request.dataset_id if dataset["status"] == "ready" else None,
                                     digest({"request": declared, "dataset": dataset, "asof_ms": job.asof_ms}))
        body = canonical(response.to_dict())
        size = len(body.encode())
        if usage[1] + historical[1] + size > job.limits.max_bytes:
            reject("LIMIT_EXCEEDED", "Fixture query bytes budget reached")
        c.execute("INSERT INTO fixture_queries VALUES(?,?,?,?,?,?)", (job.job_id, job.attempt, message.request_id,
                  fingerprint, body, size))
        return response
