"""Export a separate transport schema bundle; the existing seven schemas remain unchanged."""
import json
import types
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Literal, get_args, get_origin, get_type_hints

from ..contracts import ForecastBatch
from ..interfaces import DataRequest
from ..runtime.contracts import AnalysisJob, JobLimits, Lease, LeaseRef, ProcessingRecord, SubmissionReceipt
from ..schemas import schema_for as business_schema
from . import messages

MESSAGES = (
    messages.ClaimRequest, messages.ClaimResponse, messages.HeartbeatRequest, messages.HeartbeatResponse,
    messages.ForecastSubmission, messages.SubmissionResponse, messages.CompleteRequest, messages.CompleteResponse,
    messages.DataQueryRequest, messages.DataQueryResponse, messages.FeedbackResponse, messages.HealthResponse,
    messages.ErrorResponse,
)
RECORDS = (AnalysisJob, JobLimits, Lease, LeaseRef, ProcessingRecord, SubmissionReceipt, messages.DataRecord)


def bundle():
    definitions = {}

    def annotation_schema(annotation):
        origin, args = get_origin(annotation), get_args(annotation)
        if annotation is Any:
            return {}
        if origin is Literal:
            return {"enum": list(args)}
        if origin is types.UnionType:
            return {"anyOf": [annotation_schema(a) for a in args]}
        if annotation is type(None):
            return {"type": "null"}
        if origin is tuple:
            return {"type": "array", "items": annotation_schema(args[0])}
        if origin is dict:
            return {"type": "object", "additionalProperties": annotation_schema(args[1])}
        if annotation is str:
            return {"type": "string", "minLength": 1, "maxLength": 4096}
        if annotation is int:
            return {"type": "integer", "minimum": 0, "maximum": 2**53 - 1}
        if annotation is bool:
            return {"type": "boolean"}
        if is_dataclass(annotation):
            add(annotation)
            return {"$ref": "#/$defs/" + annotation.__name__}
        raise TypeError("Unsupported annotation: " + str(annotation))

    def add(cls):
        if cls.__name__ in definitions:
            return
        definitions[cls.__name__] = {}
        if cls is ForecastBatch:
            schema = business_schema(cls)
            definitions[cls.__name__] = {k: v for k, v in schema.items() if k not in {"$schema", "title"}}
            return
        properties = {name: annotation_schema(t) for name, t in get_type_hints(cls).items()}
        if cls is DataRequest:
            for name in ("symbols", "fields"):
                properties[name].update(minItems=1, uniqueItems=True)
            properties["max_records"]["minimum"] = 1
        if cls is AnalysisJob:
            properties["symbols"].update(minItems=1, uniqueItems=True)
        if cls is JobLimits:
            for prop in properties.values():
                prop["minimum"] = 1
        definitions[cls.__name__] = {"type": "object", "properties": properties,
                                     "required": [f.name for f in fields(cls)], "additionalProperties": False}

    for cls in (*RECORDS, *MESSAGES):
        add(cls)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "AgentTransport", "$comment": "Structural checks; Python validates cross-field invariants.",
            "$defs": definitions, "oneOf": [{"$ref": "#/$defs/" + cls.__name__} for cls in MESSAGES]}


def schema_for(cls):
    result = bundle()
    result.pop("oneOf")
    result["$ref"] = "#/$defs/" + cls.__name__
    return result


def export(path="docs/schemas/AgentTransport.schema.json"):
    Path(path).write_text(json.dumps(bundle(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    export()
