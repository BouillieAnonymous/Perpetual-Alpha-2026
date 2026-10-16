"""Offline verification of sealed synthetic risk inputs against the unchanged RiskSnapshot schema."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from perpetual_alpha.contracts import RiskSnapshot
from perpetual_alpha.market_risk import replay


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/examples/market-risk-replay.synthetic.json").read_text(encoding="utf-8"))
    result = replay(evidence)
    schema = json.loads((root / "docs/schemas/RiskSnapshot.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(result["risk_snapshot"])
    snapshot = RiskSnapshot.from_dict(result["risk_snapshot"])
    assert snapshot.status == "ready" and snapshot.simulated is True
    assert result["diagnostics"]["execution_authorized"] is False
    assert snapshot.covariance == (("0.01", "0.01"), ("0.01", "0.01"))
    print("Sealed synthetic market risk replay and unchanged RiskSnapshot schema verified; no trading")


if __name__ == "__main__":
    main()
