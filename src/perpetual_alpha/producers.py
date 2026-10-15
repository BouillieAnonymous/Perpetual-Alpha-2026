"""Explicit in-process allowlist and per-producer DataRequest scopes. No dynamic imports."""
import copy
from dataclasses import asdict

from .interfaces import DataRequest, identity
from .journal import canonical


def validate_producer(producer):
    identity(getattr(producer, "producer_id", None), getattr(producer, "producer_version", None))
    for method in ("requirements", "forecast"):
        if not callable(getattr(producer, method, None)):
            raise ValueError(f"ForecastProducer must implement {method}()")


def validate_requests(requests):
    if not isinstance(requests, tuple) or any(not isinstance(r, DataRequest) for r in requests):
        raise ValueError("requirements() must return tuple[DataRequest, ...]")
    for request in requests:
        # Validate mutable parameters and identities again at the scheduling boundary.
        DataRequest(**asdict(request))
        for sequence in (request.symbols, request.fields):
            if not isinstance(sequence, (list, tuple)):
                raise ValueError("DataRequest symbols/fields must be sequences")
            for value in sequence:
                identity(value)
            if len(set(sequence)) != len(sequence):
                raise ValueError("Duplicate DataRequest symbol/field")
    return copy.deepcopy(requests)


class ProducerRegistry:
    def __init__(self):
        self._entries = {}

    def register(self, producer):
        validate_producer(producer)
        if producer.producer_id in self._entries:
            raise ValueError("Duplicate producer_id or version conflict: " + producer.producer_id)
        self._entries[producer.producer_id] = (producer.producer_version, producer)

    def resolve(self, producer_id, producer_version=None):
        identity(producer_id)
        if producer_id not in self._entries:
            raise ValueError("Unregistered producer: " + producer_id)
        version, producer = self._entries[producer_id]
        validate_producer(producer)
        if (producer.producer_id, producer.producer_version) != (producer_id, version):
            raise ValueError("Registered producer identity changed")
        if producer_version is not None and producer_version != version:
            raise ValueError("Selected producer version mismatch")
        return producer

    def identities(self):
        return tuple(self._entries)


class ProducerPlan:
    def __init__(self, producers):
        self._scopes = {}
        merged = {}
        definitions = {}
        # A faulty shadow declaration has its own failure; conflicting dataset IDs are global errors.
        self.failures = {}
        for producer in producers:
            validate_producer(producer)
            name = producer.producer_id
            if name in self._scopes or name in self.failures:
                raise ValueError("Duplicate enabled producer")
            try:
                requirements = validate_requests(producer.requirements())
            except Exception as error:
                self.failures[name] = {"reason_code": "INVALID_PRODUCER", "reason": type(error).__name__}
                continue
            self._scopes[name] = requirements
            for request in requirements:
                definition = canonical(asdict(request))
                if request.dataset_id in definitions and definitions[request.dataset_id] != definition:
                    raise ValueError("Conflicting dataset_id: " + request.dataset_id)
                definitions[request.dataset_id] = definition
                merged[request.dataset_id] = request
        self.requests = tuple(merged.values())

    def requirements_for(self, producer_id):
        return copy.deepcopy(self._scopes[producer_id])

    def context_for(self, producer_id, context):
        ids = {r.dataset_id for r in self._scopes[producer_id]}
        return {**copy.deepcopy(context), "datasets": {key: copy.deepcopy(context["datasets"][key]) for key in ids}}
