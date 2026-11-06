"""Frozen three-candidate extension validation. Offline; no trading imports."""

import argparse
import csv
import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from study_pas_traditional17 import HOUR, Order, book_replay, generate, load, sampled_hours, utc
from study_pas_traditional17 import OUT as OLD

OUT = Path("docs/research/candidate-validation-2026-10-30")
INPUT = Path(".cache/validation-2026-10-30/pas-prefix.jsonl.gz")
START = int(datetime.fromisoformat("2026-10-27T00:00:00+00:00").timestamp() * 1000)
PAIRS = [("pas:BRENT", "TSM"), ("pas:ETH", "ROLLING_Z"), ("pas:GOLD", "FUNDING-MEAN24")]


def episodes(fills):
    previous, count = 0.0, 0
    for f in fills:
        target = f["target"]
        if abs(target) > 1e-12 and (abs(previous) < 1e-12 or target * previous < 0):
            count += 1
        previous = target
    return count


def validate_ledger(r, capital):
    s = r["summary"]
    assert abs(sum(f["fee"] for f in r["fills"]) - s["fees"]) < 1e-8
    assert abs(sum(f["friction"] for f in r["fills"]) - s["friction"]) < 1e-8
    assert abs(sum(f["amount"] for f in r["funding"]) - s["funding"]) < 1e-8
    for f in r["fills"]:
        assert abs(f["delta"] * f["reference"]) >= 10 - 1e-8
        if f["signal_ms"] is not None:
            assert f["time_ms"] > f["due_ms"] == f["signal_ms"] + HOUR
            assert f["time_ms"] <= f["due_ms"] + 90000
    peak, dd = capital, 0.0
    for e in r["equity"]:
        peak = max(peak, e["value"])
        dd = max(dd, (peak - e["value"]) / peak)
    assert abs(dd - s["max_drawdown"]) < 1e-12
    if s["complete"]:
        assert abs(sum(t["net"] for t in r["trades"]) - s["net"]) < 1e-7
        assert abs(r["equity"][-1]["value"] - capital - s["net"]) < 1e-7
        assert abs(s["mid_gross"] - s["friction"] - s["fees"] + s["funding"] - s["net"]) < 1e-7


def main(end_limit=None):
    OUT.mkdir(exist_ok=True)
    if (OUT / "results.json").exists():
        raise FileExistsError("Preserve frozen results")
    old = json.loads((OLD / "results.json").read_text())
    defs = {d["id"]: d for d in old["definitions"]}
    books, contexts, funds, settlements, manifest = load(INPUT)
    # Prove that the prior export is an unchanged prefix, not a replacement history.
    digest = hashlib.sha256()
    with gzip.open(INPUT, "rt", encoding="utf-8") as f:
        next(f)
        for line in f:
            row = json.loads(line)
            if row["seq"] > old["manifest"]["header"]["max_seq"]:
                break
            digest.update(
                (json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
            )
    assert digest.hexdigest() == old["manifest"]["prefix_sha256"]
    bars, quality = {}, {}
    for s, _ in PAIRS:
        bars[s], quality[s] = sampled_hours(s, books[s])
    end = min(v[-1].time for v in bars.values())
    if end_limit is not None:
        end = min(end, end_limit)
    results, signals, prior_checks = [], [], []
    with gzip.open(OUT / "ledgers.jsonl.gz", "wt", encoding="utf-8") as ledger:
        for s, candidate in PAIRS:
            orders = generate(defs[candidate], bars[s], funds[s], START, end)
            # Future truncation must not change decisions already made.
            cut = START + 48 * HOUR
            prefix = generate(
                defs[candidate],
                [b for b in bars[s] if b.time < cut],
                [f for f in funds[s] if f["t"] < cut],
                START,
                cut,
            )
            assert [asdict(o) for o in orders if START <= o.time < cut] == [
                asdict(o) for o in prefix if START <= o.time < cut
            ]
            baseline = generate(defs["FUNDING-LAST"], bars[s], funds[s], START, end)
            variants = {candidate: orders, "FUNDING-LAST": baseline}
            for direction, name in [(1, "FIXED-LONG"), (-1, "FIXED-SHORT")]:
                variants[name] = [
                    Order(b.time, {s: direction * 0.2}, "fixed-direction")
                    for b in bars[s]
                    if START <= b.time < end and b.time // HOUR % 12 == 0
                ]
                variants["MATCHED-" + name] = [
                    Order(o.time, {s: direction * abs(o.targets[s])}, "matched-direction", o.features)
                    for o in orders
                ]
            for rule, os in variants.items():
                signals.append(
                    dict(symbol=s, rule=rule, orders=[asdict(o) for o in os if START <= o.time < end])
                )
                for capital in (500, 10000):
                    for scenario, fee, slip in [("base", 9, 2), ("stress", 18, 4)]:
                        r = book_replay(
                            s, books[s], contexts[s], settlements[s], os, START, end, capital, fee, slip
                        )
                        validate_ledger(r, capital)
                        summary = dict(
                            symbol=s,
                            rule=rule,
                            scenario=scenario,
                            opening_episodes=episodes(r["fills"]),
                            **r["summary"],
                        )
                        results.append(summary)
                        ledger.write(
                            json.dumps(dict(symbol=s, rule=rule, capital=capital, scenario=scenario, **r))
                            + "\n"
                        )
            # Reproduce the old headline values using the extended raw history.
            a = int(datetime.fromisoformat(old["start_utc"]).timestamp() * 1000)
            b = int(datetime.fromisoformat(old["end_utc"]).timestamp() * 1000)
            os = generate(defs[candidate], bars[s], funds[s], a, b)
            for scenario, fee, slip in [("base", 9, 2), ("stress", 18, 4)]:
                r = book_replay(s, books[s], contexts[s], settlements[s], os, a, b, 500, fee, slip)
                expected = next(
                    x["summary"]
                    for x in old["book_results"]
                    if x["symbol"] == s
                    and x["rule"] == candidate
                    and x["split"] == "full"
                    and x["capital"] == 500
                    and x["scenario"] == scenario
                )
                assert abs(r["summary"]["net"] - expected["net"]) < 1e-8
                prior_checks.append(
                    dict(
                        symbol=s,
                        rule=candidate,
                        scenario=scenario,
                        net=r["summary"]["net"],
                        opening_episodes=episodes(r["fills"]),
                    )
                )
            print(s, candidate, "validated", flush=True)
    output = dict(
        start_utc=utc(START),
        end_utc=utc(end),
        manifest=manifest,
        old_prefix_unchanged=True,
        previous_headlines=prior_checks,
        hour_quality=quality,
        definitions={k: defs[k] for _, k in PAIRS},
        results=results,
        validation=dict(
            ledger_checks=len(results), future_truncation_checks=3, old_reproduction_checks=len(prior_checks)
        ),
        sources={
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path(__file__),
                Path("scripts/study_pas_traditional17.py"),
                Path("scripts/study_pas_weekly_alpha.py"),
                *sorted((OLD / "frozen17").glob("*.py")),
            ]
        },
    )
    (OUT / "results.json").write_text(json.dumps(output, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (OUT / "signals.json").write_text(json.dumps(signals, indent=2) + "\n", encoding="utf-8")
    fields = [
        "symbol",
        "rule",
        "capital",
        "scenario",
        "net",
        "net_return",
        "max_drawdown",
        "opening_episodes",
        "fills",
        "mid_gross",
        "fees",
        "friction",
        "funding",
        "complete",
        "funding_unknown",
        "unclosed_quantity",
        "rejects",
    ]
    with (OUT / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    print(json.dumps(dict(start=utc(START), end=utc(end), checks=output["validation"])))
    for r in results:
        if r["capital"] == 500:
            print(
                r["symbol"],
                r["rule"],
                r["scenario"],
                r["net"],
                r["opening_episodes"],
                r["max_drawdown"],
                r["complete"],
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--end", help="Optional UTC end for a separately saved data-quality sensitivity window"
    )
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    OUT = args.output
    main(int(datetime.fromisoformat(args.end).timestamp() * 1000) if args.end else None)
