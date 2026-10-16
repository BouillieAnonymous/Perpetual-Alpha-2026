"""Offline synthetic Portfolio -> independent Gate -> existing Mock; never a live approval."""
import json
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

from perpetual_alpha.interfaces import Decision
from perpetual_alpha.journal import report_from_dict
from perpetual_alpha.portfolio_simulation import run


def main():
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root / "docs/examples/portfolio-risk.synthetic.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as folder:
        result = run(fixture, Path(folder)/"synthetic.sqlite3", resume_supervisor=True)
        paused = run(fixture, Path(folder)/"paused.sqlite3")
    for name, payload in (("ForecastBatch", fixture["forecast"]), ("RiskVerdict", result["approval"]["verdict"]),
                          ("RiskSnapshot", fixture["state"]["risk"])):
        schema = json.loads((root / "docs/schemas" / (name+".schema.json")).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(payload)
    Decision.from_dict(result["proposal"]["decision"])
    report_from_dict(result["execution_report"])
    assert result["approval"]["verdict"]["status"] == "approved"
    assert result["execution_report"]["status"] == "completed"
    assert paused["execution_report"]["status"] == "rejected"
    assert result["real_execution_authorized"] is False
    assert result["supervision_after"]["automatic_actions"] == []
    print("Synthetic budget Portfolio, independent Risk Gate, paused Supervisor and Mock loop verified; no trading")


if __name__ == "__main__":
    main()
