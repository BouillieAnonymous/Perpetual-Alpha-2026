"""CI-only account schema/synthetic evidence validation; no account address or network required."""
import json
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

from perpetual_alpha.accounts.normalize import normalize
from perpetual_alpha.accounts.schema import account_schema
from perpetual_alpha.accounts.store import AccountStore


def main():
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root / "docs/examples/account-observation.synthetic.json").read_text(encoding="utf-8"))
    assert fixture["fixture"] == "SYNTHETIC ONLY; no real team account data"
    schema = account_schema()
    assert schema == json.loads((root / "docs/schemas/AccountObservation.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    snapshot = normalize(fixture["bundle"])
    assert snapshot == fixture["snapshot"]
    Draft202012Validator(schema).validate(snapshot)
    with tempfile.TemporaryDirectory() as folder:
        now = fixture["bundle"]["ended_ms"] + 1
        store = AccountStore(Path(folder) / "account.sqlite3", clock=lambda: now)
        store.save(fixture["bundle"])
        reader = AccountStore(store.path, read_only=True)
        result = reader.latest(snapshot["wallet"], now, max_age_ms=90000)
        assert result["status"] == "observed" and result["risk_ready"] is False
        assert result["snapshot"]["available_margin_usd"] is None
    print("Separate account observation schema, synthetic capture and read-only evidence verified; no trading")


if __name__ == "__main__":
    main()
