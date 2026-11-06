"""Validate transfer archives and rerun both information sources on the SAME PAR venue."""
import gzip
import hashlib
import json
from pathlib import Path

from perpetual_alpha import market_transfer
from perpetual_alpha.market_transfer import load_external, transfer_orders
from perpetual_alpha.offline_quant.data import read_archive, source_hash
from perpetual_alpha.offline_quant.replay import Costs, replay


def validate(root):
    output = root / "external-transfer/results"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    for name, expected in hashes.items():
        if source_hash(output / name) != expected:
            raise ValueError("Transfer output hash mismatch: " + name)
    for name, expected in manifest["inputs"].items():
        if "\\" in name or name.startswith("/") or ".." in Path(name).parts:
            raise ValueError("Nonportable or out-of-root input identity")
        if source_hash(root / name) != expected:
            raise ValueError("Transfer source hash mismatch: " + name)
    actual = hashlib.sha256(Path(market_transfer.__file__).read_text(encoding="utf-8").encode()).hexdigest()
    if actual != manifest["code"]["market_transfer_sha256"]:
        raise ValueError("Transfer analysis code version mismatch")
    runner = Path(__file__).with_name("run_market_transfer.py")
    if hashlib.sha256(runner.read_text(encoding="utf-8").encode()).hexdigest() != manifest["code"]["runner_sha256"]:
        raise ValueError("Transfer runner version mismatch")
    rows = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    keys = {(r["strategy"], r["hold_hours"], r["source"], r["split"], r["scenario"]): r for r in rows}
    if len(keys) != len(rows):
        raise ValueError("Duplicate transfer identity")
    count = 0
    with gzip.open(output / "details.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            detail = json.loads(line)
            key = tuple(detail[k] for k in ("strategy", "hold_hours", "source", "split", "scenario"))
            if any(keys[key][k] != v for k, v in detail["summary"].items()):
                raise ValueError("Transfer ledger/summary mismatch")
            s = detail["summary"]
            if s["scenario_complete"]:
                expected = (s["gross_price_pnl"] - s["fees"] - s["spread_slippage_cost"] + s["funding_scenario"]) / 10000
                if abs(expected - s["net_scenario_return"]) > 1e-9:
                    raise ValueError("Transfer PnL identity mismatch")
            if s["net_return"] is not None:
                raise ValueError("Historical OHLC cannot validate executable returns")
            count += 1
    par, funding, specs, _ = read_archive(root / "official-hourly.jsonl.gz")
    ext, _ = load_external(root / "external-transfer/external-history.jsonl.gz")
    reruns = 0
    for key, reference in keys.items():
        strategy, hold, source, split, scenario = key
        if hold != 4 or split != "full_seen" or scenario != "base":
            continue
        inputs = ext if source == "external" else par
        start, end = reference["start_ms"], reference["end_ms"]
        orders = [o for s in ("par:BTC", "par:ETH") for o in transfer_orders(inputs[s], strategy, start, end, hold=hold)]
        result = replay({s: par[s] for s in ("par:BTC", "par:ETH")}, funding, specs, orders, start, end, Costs())
        if any(reference[k] != v for k, v in result["summary"].items()):
            raise ValueError("Transfer frozen replay mismatch")
        reruns += 1
    print(f"{count} transfer scenarios verified; {reruns} source-paired PAR replays match")


if __name__ == "__main__":
    validate(Path("docs/research/new-alpha-discovery-2026-10-18"))
