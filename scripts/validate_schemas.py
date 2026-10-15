"""CI-only JSON Schema validation. Install config/requirements-ci.txt first."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from perpetual_alpha.contracts import ForecastBatch, RiskSnapshot, RiskVerdict
from perpetual_alpha.research import ARTIFACTS
from perpetual_alpha.schemas import CONTRACTS, schema_for

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


if __name__ == "__main__":
    main()
