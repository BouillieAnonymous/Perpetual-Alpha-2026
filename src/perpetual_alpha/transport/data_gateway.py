"""Local read-only historical gateway. No network calls, URLs, SQL or paths from Agents."""
import json
from dataclasses import asdict, dataclass, replace

from ..historical.contracts import INTERVALS, PRICE_UNITS, SOURCE
from ..interfaces import DataRequest
from ..journal import canonical, digest
from ..runtime.contracts import Lease, Role, WireContract, decode
from ..runtime.ingress import input_digest, validate_lease
from ..runtime.jobs import reject
from .messages import DataQueryRequest, DataQueryResponse, DataRecord

FIELDS = {"open", "high", "low", "close", "volume", "trade_count"}


@dataclass(frozen=True)
class DataPolicy(WireContract):
    policy_id: str
    producer_id: str
    producer_version: str
    role: Role
    read_symbols: tuple[str, ...]
    intervals: tuple[str, ...]
    fields: tuple[str, ...]
    max_lookback_ms: int

    def check(self):
        if (not self.read_symbols or len(set(self.read_symbols)) != len(self.read_symbols)
                or not set(self.read_symbols) <= PRICE_UNITS.keys() or not self.intervals
                or len(set(self.intervals)) != len(self.intervals) or not set(self.intervals) <= INTERVALS.keys()
                or not self.fields or len(set(self.fields)) != len(self.fields) or not set(self.fields) <= FIELDS
                or not 1 <= self.max_lookback_ms <= 86400000 * 30):
            raise ValueError("Invalid server read capability")


def active_policy(c, job, binding):
    row = c.execute("SELECT payload FROM gateway_policies WHERE producer_id=? AND producer_version=? AND role=?",
                    (job.producer_id, job.producer_version, job.role)).fetchone()
    if row is None or json.loads(row[0]) != binding["policy"]:
        reject("FORBIDDEN", "Job read capability was revoked or changed")
    return DataPolicy.from_dict(binding["policy"])


def bind_job(c, job, binding):
    binding = json.loads(canonical(binding))
    if set(binding) != {"policy", "snapshot"} or set(binding["snapshot"]) != {"asof_ms", "snapshot_seq", "anchor"}:
        reject("INVALID_CONTEXT", "Invalid history binding")
    decode(int, binding["snapshot"]["snapshot_seq"])
    anchor = binding["snapshot"]["anchor"]
    if not isinstance(anchor, str) or len(anchor) != 64 or any(ch not in "0123456789abcdef" for ch in anchor):
        reject("INVALID_CONTEXT", "Invalid history anchor")
    active_policy(c, job, binding)
    if binding["snapshot"]["asof_ms"] != job.asof_ms:
        reject("INVALID_CONTEXT", "History snapshot cutoff mismatch")
    c.execute("INSERT INTO gateway_bindings VALUES(?,?)", (job.job_id, canonical(binding)))


def response_digest(response):
    # Dataset content identity is independent of the per-attempt HTTP request ID.
    return digest({k: v for k, v in response.to_dict().items() if k not in {"request_id", "payload_digest"}})


def enrich_manifest(c, job_row, job, attempt):
    base = json.loads(job_row["manifest"])
    binding = c.execute("SELECT payload FROM gateway_bindings WHERE job_id=?", (job.job_id,)).fetchone()
    if binding is None:
        return base, job_row["manifest_digest"]
    if input_digest(base["context"], tuple(decode(DataRequest, r) for r in base["requests"])) != job_row["manifest_digest"]:
        reject("INVALID_CONTEXT", "Base manifest changed")
    active_policy(c, job, json.loads(binding[0]))
    base["data_binding"] = json.loads(binding[0])
    entries = c.execute("SELECT DISTINCT d.* FROM gateway_datasets d JOIN gateway_reads r USING(job_id,dataset_id) "
                        "WHERE d.job_id=? AND r.attempt=? ORDER BY d.dataset_id", (job.job_id, attempt)).fetchall()
    for entry in entries:
        request = json.loads(entry["request"])
        response = DataQueryResponse.from_dict(json.loads(entry["response"]))
        if (digest(request) != entry["request_digest"] or response_digest(response) != response.payload_digest
                or digest({"response": response.to_dict(), "provenance": json.loads(entry["provenance"])}) != entry["response_digest"]):
            reject("INVALID_CONTEXT", "Saved evidence digest mismatch")
        dataset_id = request["dataset_id"]
        if dataset_id in base["context"]["datasets"]:
            reject("ID_CONFLICT", "Evidence dataset collides with initial context")
        base["requests"].append(request)
        base["context"]["datasets"][dataset_id] = {
            "source": request["source"], "kind": request["kind"], "parameters": request["parameters"],
            "required": request["required"], "status": response.status, "error": response.error,
            "truncated": response.truncated, "units": response.units,
            "records": [r.to_dict() for r in response.records], "payload_digest": response.payload_digest,
            "provenance": json.loads(entry["provenance"])}
    return base, input_digest(base["context"], tuple(decode(DataRequest, r) for r in base["requests"]))


def accepted_manifest(c, job_row):
    sealed = c.execute("SELECT payload,payload_digest FROM accepted_manifests WHERE job_id=?", (job_row["job_id"],)).fetchone()
    if sealed is None:
        if c.execute("SELECT 1 FROM gateway_bindings WHERE job_id=?", (job_row["job_id"],)).fetchone():
            reject("INVALID_CONTEXT", "Dynamic job has no accepted manifest")
        return json.loads(job_row["manifest"])
    manifest = json.loads(sealed["payload"])
    if digest(manifest) != sealed["payload_digest"]:
        reject("INVALID_CONTEXT", "Accepted manifest digest mismatch")
    return manifest


class DataGateway:
    def __init__(self, queue, history):
        if not history.read_only:
            raise ValueError("Gateway requires a read-only history handle")
        self.queue, self.history = queue, history

    def configure(self, policy):
        policy = DataPolicy.from_dict(policy.to_dict())
        with self.queue.store.transaction() as (c, now):
            key = (policy.producer_id, policy.producer_version, policy.role)
            old = c.execute("SELECT payload FROM gateway_policies WHERE producer_id=? AND producer_version=? AND role=?", key).fetchone()
            if old and json.loads(old[0])["policy_id"] == policy.policy_id and json.loads(old[0]) != policy.to_dict():
                raise ValueError("Policy ID is immutable; choose a new policy version")
            c.execute("INSERT INTO gateway_policies VALUES(?,?,?,?) ON CONFLICT(producer_id,producer_version,role) "
                      "DO UPDATE SET payload=excluded.payload", (*key, canonical(policy.to_dict())))

    def binding(self, policy, asof_ms):
        return {"policy": policy.to_dict(), "snapshot": self.history.pin(asof_ms)}

    def _guard(self, c, principal, message, now):
        principal = self.queue._auth(c, principal)
        row, job = self.queue._owned(c, message.lease.job_id, principal)
        if row["lease"] is None:
            reject("LEASE_LOST", "No active lease")
        lease = Lease.from_dict(json.loads(row["lease"]))
        validate_lease(job, lease, message.lease, principal, now)
        if now - lease.claimed_ms > job.limits.max_inference_ms or not 0 <= now - job.asof_ms <= job.limits.max_input_age_ms:
            reject("EXPIRED", "Job analysis/input time budget reached")
        self.queue._control(c, job)
        saved = c.execute("SELECT payload FROM gateway_bindings WHERE job_id=?", (job.job_id,)).fetchone()
        if saved is None:
            reject("FORBIDDEN", "Job has no historical read capability")
        binding = json.loads(saved[0])
        policy = active_policy(c, job, binding)
        request = message.data_request
        if (request.source != SOURCE or request.kind != "candle" or not set(request.symbols) <= set(policy.read_symbols)
                or not set(request.fields) <= set(policy.fields) or set(request.parameters) != {"interval", "end_ms"}
                or request.parameters["interval"] not in policy.intervals
                or not 0 < request.lookback_ms <= policy.max_lookback_ms
                or request.max_records > min(5000, job.limits.max_records_per_query)
                or request.dataset_id in json.loads(row["manifest"])["context"]["datasets"]):
            reject("FORBIDDEN", "Query exceeds server allowlist")
        interval, end = request.parameters["interval"], request.parameters["end_ms"]
        step = INTERVALS[interval]
        if (type(end) is not int or not 0 <= end <= job.asof_ms or end % step or request.lookback_ms % step
                or end < request.lookback_ms or request.lookback_ms // step * len(request.symbols) > 10000):
            reject("INVALID_REQUEST", "Invalid/future historical window")
        if request.max_age_ms > policy.max_lookback_ms:
            reject("FORBIDDEN", "Requested freshness exceeds Job policy")
        return job, binding

    def _cached(self, c, job, message):
        request = message.data_request
        fingerprint = digest(asdict(request))
        if c.execute("SELECT 1 FROM fixture_queries WHERE job_id=? AND attempt=? AND request_id=?",
                     (job.job_id, job.attempt, message.request_id)).fetchone():
            reject("ID_CONFLICT", "Request ID already belongs to a fixture query")
        alias = c.execute("SELECT * FROM gateway_reads WHERE job_id=? AND attempt=? AND request_id=?",
                          (job.job_id, job.attempt, message.request_id)).fetchone()
        if alias and alias["request_digest"] != fingerprint:
            reject("ID_CONFLICT", "Query request ID changed content")
        saved = c.execute("SELECT * FROM gateway_datasets WHERE job_id=? AND dataset_id=?",
                          (job.job_id, request.dataset_id)).fetchone()
        if saved and saved["request_digest"] != fingerprint:
            reject("ID_CONFLICT", "Dataset ID definition is immutable across attempts")
        return saved, alias

    def _save_read(self, c, job, message, alias):
        if alias:
            return
        count = c.execute("SELECT count(*) FROM gateway_reads WHERE job_id=?", (job.job_id,)).fetchone()[0]
        if count >= job.limits.max_queries * 4:
            reject("LIMIT_EXCEEDED", "Query alias budget reached")
        c.execute("INSERT INTO gateway_reads VALUES(?,?,?,?,?)", (job.job_id, job.attempt, message.request_id,
                  message.data_request.dataset_id, digest(asdict(message.data_request))))

    @staticmethod
    def _usage(c, job):
        historical = c.execute("SELECT count(*),coalesce(sum(response_bytes),0) FROM gateway_datasets WHERE job_id=?", (job.job_id,)).fetchone()
        fixture = c.execute("SELECT count(*),coalesce(sum(response_bytes),0) FROM fixture_queries WHERE job_id=?", (job.job_id,)).fetchone()
        return historical[0] + fixture[0], historical[1] + fixture[1]

    def _read(self, job, binding, message):
        request, snapshot = message.data_request, binding["snapshot"]
        end, interval = request.parameters["end_ms"], request.parameters["interval"]
        remaining = request.max_records
        records, provenance = [], {}
        for symbol in request.symbols:
            result = self.history.read_candles(symbol, interval, end - request.lookback_ms, end, job.asof_ms,
                                               max_records=max(1, remaining), max_age_ms=request.max_age_ms,
                                               snapshot_seq=snapshot["snapshot_seq"], snapshot_anchor=snapshot["anchor"])
            provenance[symbol] = result
            selected = result["records"][:remaining]
            for row in selected:
                records.append(DataRecord("candle:" + digest({"symbol": symbol, "revision": row["revision_id"],
                                                               "anchor": snapshot["anchor"]}), symbol, row["close_ms"],
                                          row["received_ms"], {f: str(row[f]) for f in request.fields}))
            remaining -= len(selected)
        complete = all(r["status"] == "ready" and not r["gaps"] and not r["invalid"] and not r["truncated"]
                       and not r["not_yet_closed_records"] for r in provenance.values())
        truncated = any(r["truncated"] for r in provenance.values()) or sum(r["available_records"] for r in provenance.values()) > request.max_records
        complete = complete and not truncated and bool(records)
        status = "ready" if complete else "stale" if any(r["status"] == "stale" for r in provenance.values()) else (
            "missing" if not records else "error")
        # Existing DTO intentionally has no partial state. Keep structured bounded diagnostics in error.
        diagnostics = {s: {"status": r["status"], "gaps": r["gaps"][:1], "gap_count": len(r["gaps"]),
                           "invalid_count": len(r["invalid"]), "truncated": r["truncated"]}
                       for s, r in provenance.items()}
        units = {f"{symbol}/{field}": ("count" if field == "trade_count" else "venue-size" if field == "volume"
                                      else PRICE_UNITS[symbol]) for symbol in request.symbols for field in request.fields}
        response = DataQueryResponse(message.request_id, request.dataset_id, request.source, request.kind,
                                     "candle-gateway/0.1", job.asof_ms, status, units, tuple(records), truncated,
                                     None if complete else canonical({"code": "HISTORY_NOT_READY", "quality": diagnostics}),
                                     request.dataset_id if complete else None, "0" * 64)
        return replace(response, payload_digest=response_digest(response)), provenance

    def query(self, principal, message):
        message = DataQueryRequest.from_dict(message.to_dict())
        # Authorize before local history reads. Never hold Runtime write lock over the history query.
        with self.queue.store.transaction() as (c, now):
            job, binding = self._guard(c, principal, message, now)
            saved, alias = self._cached(c, job, message)
            if saved:
                response = DataQueryResponse.from_dict(json.loads(saved["response"]))
                if response_digest(response) != response.payload_digest or digest({"response": response.to_dict(),
                        "provenance": json.loads(saved["provenance"])}) != saved["response_digest"]:
                    reject("INVALID_CONTEXT", "Cached response changed")
                self._save_read(c, job, message, alias)
                return replace(response, request_id=message.request_id)
            if self._usage(c, job)[0] >= job.limits.max_queries:
                reject("LIMIT_EXCEEDED", "Job query budget reached")
        try:
            response, provenance = self._read(job, binding, message)
        except ValueError:
            reject("UNAVAILABLE", "Pinned local history could not be read")
        with self.queue.store.transaction() as (c, now):
            job, current = self._guard(c, principal, message, now)
            if current != binding:
                reject("INVALID_CONTEXT", "Job binding changed")
            saved, alias = self._cached(c, job, message)
            if saved:
                value = DataQueryResponse.from_dict(json.loads(saved["response"]))
                if digest({"response": value.to_dict(), "provenance": json.loads(saved["provenance"])}) != saved["response_digest"]:
                    reject("INVALID_CONTEXT", "Concurrent saved response changed")
                self._save_read(c, job, message, alias)
                return replace(value, request_id=message.request_id)
            usage = self._usage(c, job)
            body = canonical(response.to_dict())
            size = len(body.encode()) + len(canonical(provenance).encode()) + len(canonical(asdict(message.data_request)).encode())
            if usage[0] >= job.limits.max_queries or usage[1] + size > job.limits.max_bytes:
                reject("LIMIT_EXCEEDED", "Job query count/bytes budget reached")
            c.execute("INSERT INTO gateway_datasets VALUES(?,?,?,?,?,?,?,?)", (job.job_id, message.data_request.dataset_id,
                      canonical(asdict(message.data_request)), digest(asdict(message.data_request)), body,
                      digest({"response": response.to_dict(), "provenance": provenance}), canonical(provenance), size))
            self._save_read(c, job, message, alias)
            self.queue.store.event(c, now, "data-sealed", {"job_id": job.job_id, "dataset_id": response.dataset_id,
                                                          "payload_digest": response.payload_digest, "attempt": job.attempt})
            return response
