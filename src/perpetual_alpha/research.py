"""Versioned offline research artifacts and explicit human review records; no deployment capability."""
import json
from dataclasses import dataclass

from .contracts import JsonContract, symbol
from .interfaces import decimal_string, identity, timestamp
from .journal import canonical


def research_identity(artifact_id, candidate_id, version, created_ms, schema, expected):
    identity(artifact_id, candidate_id, version)
    timestamp(created_ms)
    if schema != expected:
        raise ValueError("Unsupported research schema")


@dataclass(frozen=True)
class ResearchTask(JsonContract):
    task_id: str
    hypothesis: str
    creator: str
    created_ms: int
    symbols: tuple[str, ...]
    data_requirements: tuple[str, ...]
    schema_version: str = "research-task/0.1"

    def __post_init__(self):
        identity(self.task_id, self.hypothesis, self.creator)
        timestamp(self.created_ms)
        if self.schema_version != "research-task/0.1":
            raise ValueError("Unsupported task schema")
        if not isinstance(self.symbols, (tuple, list)) or not self.symbols:
            raise ValueError("Research symbols required")
        for s in self.symbols:
            symbol(s)
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Duplicate research symbol")
        if not isinstance(self.data_requirements, (tuple, list)):
            raise ValueError("Data requirements must be a sequence")
        for requirement in self.data_requirements:
            identity(requirement)
        object.__setattr__(self, "symbols", tuple(self.symbols))
        object.__setattr__(self, "data_requirements", tuple(self.data_requirements))


@dataclass(frozen=True)
class StrategyCandidate(JsonContract):
    candidate_id: str
    task_id: str
    version: str
    created_ms: int
    forecast_horizon_ms: int
    rules_or_config: dict
    code_version: str
    dependency_version: str
    schema_version: str = "strategy-candidate/0.1"

    def __post_init__(self):
        from .contracts import positive_ms
        research_identity(self.candidate_id, self.task_id, self.version, self.created_ms,
                          self.schema_version, "strategy-candidate/0.1")
        identity(self.code_version, self.dependency_version)
        positive_ms(self.forecast_horizon_ms)
        if not isinstance(self.rules_or_config, dict):
            raise ValueError("Candidate rules/config must be a JSON object")
        canonical(self.rules_or_config)


@dataclass(frozen=True)
class ExperimentRecord(JsonContract):
    experiment_id: str
    candidate_id: str
    candidate_version: str
    created_ms: int
    data_snapshot_ref: str
    data_available_ms: int
    splits: dict[str, tuple[int, int]]
    cost_assumptions: str
    fill_assumptions: str
    parameter_search_count: int
    metrics: dict[str, str]
    failures: tuple[str, ...]
    reproduce_entrypoint: str
    schema_version: str = "experiment/0.1"

    def __post_init__(self):
        research_identity(self.experiment_id, self.candidate_id, self.candidate_version, self.created_ms,
                          self.schema_version, "experiment/0.1")
        identity(self.data_snapshot_ref, self.cost_assumptions, self.fill_assumptions, self.reproduce_entrypoint)
        timestamp(self.data_available_ms)
        if self.data_available_ms > self.created_ms:
            raise ValueError("Experiment uses unavailable future data")
        if not isinstance(self.splits, dict) or set(self.splits) != {"train", "validation", "test"}:
            raise ValueError("Train/validation/test splits required")
        intervals = []
        for name in ("train", "validation", "test"):
            interval = self.splits[name]
            if not isinstance(interval, (tuple, list)) or len(interval) != 2:
                raise ValueError("Split must be [start_ms,end_ms]")
            start, end = interval
            timestamp(start)
            timestamp(end)
            if not start < end <= self.data_available_ms:
                raise ValueError("Invalid research interval")
            intervals.append((start, end))
        if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
            raise ValueError("Overlapping or unordered research splits")
        object.__setattr__(self, "splits", {name: tuple(self.splits[name]) for name in self.splits})
        if type(self.parameter_search_count) is not int or self.parameter_search_count < 0:
            raise ValueError("Invalid search count")
        if not isinstance(self.metrics, dict):
            raise ValueError("Metrics must be a mapping")
        for name, value in self.metrics.items():
            identity(name)
            decimal_string(value)
        if not isinstance(self.failures, (tuple, list)):
            raise ValueError("Failures must be a sequence")
        for failure in self.failures:
            identity(failure)
        object.__setattr__(self, "failures", tuple(self.failures))


@dataclass(frozen=True)
class StrategyApproval(JsonContract):
    approval_id: str
    candidate_id: str
    candidate_version: str
    created_ms: int
    status: str
    reviewer: str
    basis: str
    experiment_id: str
    deployment_scope: str = "offline-only"
    schema_version: str = "strategy-approval/0.1"

    def __post_init__(self):
        research_identity(self.approval_id, self.candidate_id, self.candidate_version, self.created_ms,
                          self.schema_version, "strategy-approval/0.1")
        identity(self.reviewer, self.basis, self.experiment_id)
        if self.status not in {"evaluated", "rejected", "approved", "retired"}:
            raise ValueError("Invalid review status")
        if self.deployment_scope != "offline-only":
            raise ValueError("This registry cannot approve live deployment")


ARTIFACTS = {"task": (ResearchTask, "task_id"), "candidate": (StrategyCandidate, "candidate_id"),
             "experiment": (ExperimentRecord, "experiment_id"), "approval": (StrategyApproval, "approval_id")}


class ResearchRegistry:
    def __init__(self, journal):
        self.journal = journal

    def get(self, kind, artifact_id):
        cls, _ = ARTIFACTS[kind]
        row = self.journal.connection.execute("SELECT payload FROM artifacts WHERE kind=? AND artifact_id=?",
                                             (kind, artifact_id)).fetchone()
        if row is None:
            raise KeyError((kind, artifact_id))
        return cls.from_dict(json.loads(row[0]))

    def status(self, candidate_id):
        self.get("candidate", candidate_id)
        rows = self.journal.connection.execute("SELECT payload FROM artifacts WHERE kind='approval' ORDER BY rowid")
        statuses = [json.loads(row[0])["status"] for row in rows if json.loads(row[0])["candidate_id"] == candidate_id]
        return statuses[-1] if statuses else "draft"

    def save(self, kind, artifact):
        cls, key = ARTIFACTS[kind]
        artifact = cls.from_dict(artifact.to_dict())
        artifact_id = getattr(artifact, key)
        connection = self.journal.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT payload FROM artifacts WHERE kind=? AND artifact_id=?",
                                     (kind, artifact_id)).fetchone()
            payload = canonical(artifact.to_dict())
            if row:
                if row[0] != payload:
                    raise ValueError("Immutable research artifact ID conflict")
                connection.commit()
                return artifact
            if kind == "candidate":
                task = self.get("task", artifact.task_id)
                if task.created_ms > artifact.created_ms:
                    raise ValueError("Candidate predates task")
            elif kind in {"experiment", "approval"}:
                candidate = self.get("candidate", artifact.candidate_id)
                if artifact.candidate_version != candidate.version or artifact.created_ms < candidate.created_ms:
                    raise ValueError("Candidate version/time mismatch")
                if kind == "approval":
                    experiment = self.get("experiment", artifact.experiment_id)
                    if (experiment.candidate_id != candidate.candidate_id
                            or experiment.candidate_version != candidate.version
                            or experiment.created_ms > artifact.created_ms):
                        raise ValueError("Review experiment mismatch")
                    allowed = {"draft": {"evaluated", "rejected"}, "evaluated": {"approved", "rejected"},
                               "approved": {"retired"}, "rejected": set(), "retired": set()}
                    if artifact.status not in allowed[self.status(candidate.candidate_id)]:
                        raise ValueError("Invalid manual review transition")
                    prior = [json.loads(row[0])["created_ms"] for row in connection.execute(
                        "SELECT payload FROM artifacts WHERE kind='approval'")
                        if json.loads(row[0])["candidate_id"] == candidate.candidate_id]
                    if prior and artifact.created_ms < max(prior):
                        raise ValueError("Review time moved backwards")
            connection.execute("INSERT INTO artifacts VALUES(?,?,?)", (kind, artifact_id, payload))
            self.journal.record("research-" + kind, artifact.to_dict(), artifact.created_ms)
            connection.commit()
            return artifact
        except Exception:
            connection.rollback()
            raise
