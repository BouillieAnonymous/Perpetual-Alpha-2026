"""Offline archive/ledger integrity plus actual replays; no optional numerical libraries needed."""
import gzip
import json
from pathlib import Path

from perpetual_alpha.offline_quant.data import read_archive, source_hash
from perpetual_alpha.offline_quant.replay import Costs, replay
from perpetual_alpha.offline_quant.run import orders_for, source_digest


def validate(root):
    archive = root / "official-hourly.jsonl.gz"
    output = root / "experiments"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    hashes = json.loads((output / "artifact-hashes.json").read_text(encoding="utf-8"))
    for name, expected in hashes.items():
        if source_hash(output / name) != expected:
            raise ValueError("Research artifact digest mismatch: " + name)
    if source_hash(archive) != manifest["input_sha256"] or source_digest() != manifest["research_source_sha256"]:
        raise ValueError("Input/research source version mismatch")
    bars, funding, specs, _ = read_archive(archive)
    summaries = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    keys = {(r["strategy_id"], r["split"], r["scenario"]): r for r in summaries}
    if len(keys) != len(summaries):
        raise ValueError("Duplicate experiment identity")
    with gzip.open(output / "details.jsonl.gz", "rt", encoding="utf-8") as stream:
        count = 0
        for line in stream:
            detail = json.loads(line)
            key = detail["strategy_id"], detail["split"], detail["scenario"]
            summary = detail["summary"]
            if any(keys[key][field] != value for field, value in summary.items()):
                raise ValueError("Detail/summary disagreement")
            if summary["net_return"] is not None or summary["executable_net_return"] is not None:
                raise ValueError("OHLC research cannot claim executable net returns")
            if summary["scenario_complete"]:
                pnl = summary["gross_price_pnl"] - summary["fees"] - summary["spread_slippage_cost"] + summary["funding_scenario"]
                if abs(pnl / summary["initial_capital"] - summary["net_scenario_return"]) > 1e-9:
                    raise ValueError("Summary cash identity failed")
                if abs(sum(t["net_scenario"] for t in detail["trades"]) - pnl) > 1e-6:
                    raise ValueError("Closed trade allocation failed")
            if any(f["price_proxy_time_ms"] > f["time_ms"] for f in detail["funding"]):
                raise ValueError("Future settlement price proxy")
            count += 1
    selected = {"DONCHIAN", "DONCHIAN_COMPRESSION_EXPANSION", "XSM-TEN-PRICE-SCENARIO-RISK",
                "XSM-BTC-ETH-SUBSET-RISK", "FUNDING-LAST", "PAIR_RATIO", "REGIME-RANDOM"}
    replays = 0
    for definition in manifest["definitions"]:
        if definition["id"] not in selected:
            continue
        for split in ("full_seen", "holdout_seen"):
            start, end = manifest["splits_ms"][split]
            orders = orders_for(definition, bars, funding, manifest["pair_training"], start, end)
            result = replay({s: bars[s] for s in definition["symbols"]}, funding, specs, orders, start, end, Costs())
            reference = keys[definition["id"], split, "base"]
            if any(reference[k] != v for k, v in result["summary"].items()):
                raise ValueError("Frozen experiment replay mismatch: " + definition["id"])
            replays += 1
    print(f"{count} archived experiment identities/ledgers checked; {replays} actual offline replays match")


if __name__ == "__main__":
    validate(Path("docs/research/new-alpha-discovery-2026-10-18"))
