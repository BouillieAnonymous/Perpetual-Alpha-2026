"""CI-only JSON Schema validation. Install config/requirements-ci.txt first."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from perpetual_alpha.contracts import ForecastBatch, RiskSnapshot, RiskVerdict
from perpetual_alpha.research import ARTIFACTS
from perpetual_alpha.schemas import CONTRACTS, schema_for
from perpetual_alpha.transport.schemas import MESSAGES, bundle
from perpetual_alpha.transport.schemas import schema_for as transport_schema

ROOT = Path(__file__).resolve().parents[1]


def main():
    validators = {}
    for cls in CONTRACTS:
        schema = json.loads((ROOT / "docs/schemas" / (cls.__name__ + ".schema.json")).read_text(encoding="utf-8"))
        if schema != schema_for(cls):
            raise ValueError(f"Stale exported schema: {cls.__name__}; run python -m perpetual_alpha.schemas")
        Draft202012Validator.check_schema(schema)
        validators[cls] = Draft202012Validator(schema)
    example = json.loads((ROOT / "docs/examples/dry-run.json").read_text(encoding="utf-8"))
    for batch in example["forecasts"]:
        validators[ForecastBatch].validate(batch)
        ForecastBatch.from_dict(batch)
    for cls, key in ((RiskSnapshot, "risk_snapshot"), (RiskVerdict, "risk_verdict")):
        validators[cls].validate(example[key])
        cls.from_dict(example[key])
    research = json.loads((ROOT / "docs/examples/research-artifacts.json").read_text(encoding="utf-8"))
    for kind, payload in research.items():
        cls, _ = ARTIFACTS[kind]
        validators[cls].validate(payload)
        cls.from_dict(payload)
    print(f"Validated {len(validators)} JSON Schemas and all documented contract examples")
    saved = json.loads((ROOT / "docs/schemas/AgentTransport.schema.json").read_text(encoding="utf-8"))
    if saved != bundle():
        raise ValueError("Stale transport schema; run python -m perpetual_alpha.transport.schemas")
    Draft202012Validator.check_schema(saved)
    examples = json.loads((ROOT / "docs/examples/agent-transport.json").read_text(encoding="utf-8"))
    if set(examples) != {cls.__name__ for cls in MESSAGES}:
        raise ValueError("Missing transport message example")
    for cls in MESSAGES:
        Draft202012Validator(transport_schema(cls)).validate(examples[cls.__name__])
        Draft202012Validator(saved).validate(examples[cls.__name__])
        cls.from_dict(examples[cls.__name__])
    print(f"Validated transport bundle and {len(MESSAGES)} request/response examples")


if __name__ == "__main__":
    main()
