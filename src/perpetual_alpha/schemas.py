"""Export structural JSON Schemas. Cross-field temporal/numeric invariants remain in Python validators."""
import json
import types
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

from .contracts import Forecast, ForecastBatch, RiskSnapshot, RiskVerdict
from .research import ExperimentRecord, ResearchTask, StrategyApproval, StrategyCandidate

CONTRACTS = (ForecastBatch, RiskSnapshot, RiskVerdict, ResearchTask, StrategyCandidate, ExperimentRecord, StrategyApproval)


def type_schema(annotation):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is types.UnionType:
        return {"anyOf": [type_schema(arg) for arg in args]}
    if annotation is type(None):
        return {"type": "null"}
    if is_dataclass(annotation):
        return object_schema(annotation)
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return {"type": "array", "items": type_schema(args[0])}
        return {"type": "array", "prefixItems": [type_schema(a) for a in args],
                "minItems": len(args), "maxItems": len(args)}
    if origin is dict:
        return {"type": "object", "additionalProperties": type_schema(args[1])}
    return {"type": {str: "string", int: "integer", bool: "boolean", dict: "object"}[annotation]}


def object_schema(cls):
    properties = {name: type_schema(annotation) for name, annotation in get_type_hints(cls).items()}
    for name, prop in properties.items():
        if name.endswith("_ms") or name in {"sample_count", "parameter_search_count"}:
            prop["minimum"] = 0
        if name in {"horizon_ms", "forecast_horizon_ms", "estimation_window_ms", "return_interval_ms"}:
            prop["minimum"] = 1
        if name == "schema_version":
            prop["const"] = next(f.default for f in fields(cls) if f.name == name)
        if name == "symbol":
            prop["pattern"] = "^par:.+$"
        if name == "symbols":
            prop.update(minItems=1, uniqueItems=True)
            prop["items"]["pattern"] = "^par:.+$"
        if name.endswith("_id") or name.endswith("_version") or name in {"version", "creator", "hypothesis", "reviewer", "basis"}:
            prop["minLength"] = 1
    required = list(properties)
    schema = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    if cls is Forecast:
        optional = {"reason", "expected_return", "prob_up", "prob_down", "forecast_vol", "quantiles",
                    "distribution_ref", "calibration_id", "model_version", "prompt_version"}
        schema["required"] = [name for name in required if name not in optional]
        properties["status"]["enum"] = ["ready", "abstain", "error"]
        properties["direction"] = {"enum": ["up", "down", "neutral", None]}
        for name in ("expected_return", "prob_up", "prob_down", "forecast_vol"):
            properties[name] = {"anyOf": [{"type": "string", "pattern":
                                         r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"},
                                        {"type": "null"}]}
        schema["allOf"] = [{"if": {"properties": {"status": {"const": "ready"}}},
                            "then": {"properties": {"direction": {"enum": ["up", "down", "neutral"]}}},
                            "else": {"properties": {"direction": {"type": "null"},
                                                     **{n: {"type": "null"} for n in (
                                                         "expected_return", "prob_up", "prob_down", "forecast_vol",
                                                         "quantiles", "distribution_ref")}}}}]
    elif cls is RiskSnapshot:
        properties["status"]["enum"] = ["ready", "missing", "stale", "error"]
        properties["units"]["const"] = "simple-return-per-interval"
        properties["annualization"]["const"] = "none"
    elif cls is RiskVerdict:
        from .interfaces import REASON_CODES
        properties["status"]["enum"] = ["approved", "rejected", "deferred"]
        properties["reason_code"]["enum"] = sorted(REASON_CODES)
        properties["evidence"]["minItems"] = 1
        properties["decision_digest"]["pattern"] = "^[0-9a-f]{64}$"
        schema["allOf"] = [{"if": {"properties": {"status": {"const": "approved"}}},
                            "then": {"properties": {"reason_code": {"const": "OK"}}},
                            "else": {"properties": {"reason_code": {"not": {"const": "OK"}}}}}]
    elif cls is StrategyApproval:
        properties["status"]["enum"] = ["evaluated", "rejected", "approved", "retired"]
        properties["deployment_scope"]["const"] = "offline-only"
    elif cls is ExperimentRecord:
        properties["splits"] = {"type": "object", "required": ["train", "validation", "test"],
                                "properties": {name: {"type": "array", "items": {"type": "integer", "minimum": 0},
                                                      "minItems": 2, "maxItems": 2}
                                               for name in ("train", "validation", "test")},
                                "additionalProperties": False}
    return schema


def schema_for(cls):
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": cls.__name__,
            "$comment": "Structural validation only; use Python contract validators for cross-field invariants.",
            **object_schema(cls)}


def export(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for cls in CONTRACTS:
        (directory / (cls.__name__ + ".schema.json")).write_text(
            json.dumps(schema_for(cls), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    export("docs/schemas")
