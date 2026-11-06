"""Causal/identity/depth and accounting checks for the original-17 PAS study."""

import gzip
import json
import math
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from study_pas_traditional17 import HOUR, OUT, Bar, Order, book_replay, generate, load


def synthetic_bars(n=210, symbol="pas:BTC"):
    return [
        Bar(
            symbol,
            (i + 1) * HOUR,
            100 + math.sin(i / 4),
            102 + math.sin(i / 4),
            98 + math.sin(i / 4),
            100 + math.sin(i / 4),
            0.0,
            (i + 1) * HOUR,
        )
        for i in range(n)
    ]


def checks():
    defs = json.loads((OUT / "definitions.json").read_text())
    bars = synthetic_bars()
    rates = [
        dict(event=i * HOUR + 10, t=i * HOUR + 300000, rate=(-1 if i % 7 else 1) * 0.0001)
        for i in range(1, 210)
    ]
    # Exact source version matches the extracted package for every non-Funding rule.
    sys.path.insert(0, str(Path(".cache/new-alpha-discovery-pr/src").resolve()))
    from perpetual_alpha.offline_quant.run import orders_for as original

    for d in defs:
        if d["kind"] == "funding":
            continue
        actual = generate(d, bars, rates, 0, 200 * HOUR)
        expected = original({**d, "symbols": ["pas:BTC"]}, {"pas:BTC": bars}, {}, {}, 0, 200 * HOUR)
        assert [asdict(o) for o in actual] == [asdict(o) for o in expected], d["id"]
        prefix = generate(d, bars[:150], rates, 0, 150 * HOUR)
        assert [asdict(o) for o in actual if o.time < 150 * HOUR] == [asdict(o) for o in prefix]
    for d in [x for x in defs if x["kind"] == "funding"]:
        a = generate(d, bars, rates, 0, 200 * HOUR)
        b = generate(d, bars, rates + [dict(event=11 * HOUR, t=201 * HOUR, rate=0.5)], 0, 200 * HOUR)
        assert [asdict(o) for o in a] == [asdict(o) for o in b]

    def q(i):
        return dict(
            t=i * HOUR + 1000,
            event=i * HOUR + 1000,
            bid=99.0 + i,
            ask=100.0 + i,
            mid=99.5 + i,
            bs=100.0,
            az=100.0,
            decimals=3,
            seq=i,
        )

    quotes = [q(i) for i in range(1, 4)]
    contexts = [dict(t=i * HOUR, oracle=100.0, seq=i) for i in range(1, 4)]
    events = [dict(event=i * HOUR + 500, rate=0.001) for i in (2, 3)]
    orders = [Order(0, {"pas:BTC": 0.1}, "entry")]
    result = book_replay("pas:BTC", quotes, contexts, events, orders, 0, 4 * HOUR, 1000)
    assert result["summary"]["complete"] and len(result["fills"]) == 2
    assert result["funding"][0]["amount"] < 0
    assert result["fills"][0]["time_ms"] > HOUR
    quotes[-1]["bs"] = 0.001
    failed = book_replay("pas:BTC", quotes, contexts, events, orders, 0, 4 * HOUR, 1000)
    assert not failed["summary"]["complete"] and failed["summary"]["net"] is None
    print(
        "17-rule source equivalence, causal prefix/Funding receipt, fill delay and unresolved-depth checks passed."
    )


def verify():
    books, _, _, _, manifest = load(Path(".cache/alpha-week-2026-10-26/pas-prefix.jsonl.gz"))
    quotes = {q["seq"]: q for qs in books.values() for q in qs}
    results = json.loads((OUT / "results.json").read_text())
    for source, sha in results["source_sha256"].items():
        import hashlib

        assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == sha
    records = fills = 0
    with gzip.open(OUT / "book-ledgers.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            charges = friction = Decimal(0)
            q = Decimal(0)
            for x in r["fills"]:
                raw = quotes[x["seq"]]
                assert raw["t"] == x["time_ms"] and raw["mid"] == x["reference"]
                assert raw["bid"] == x["bid"] and raw["ask"] == x["ask"]
                assert abs(x["delta"]) <= raw["az" if x["delta"] > 0 else "bs"] + 1e-12
                assert abs(x["delta"] * raw["mid"]) >= 10 - 1e-8
                step = Decimal(10) ** (-manifest["specs"][r["symbol"]])
                assert Decimal(str(x["target"])) % step == 0
                assert abs(x["target"] * raw["mid"]) <= 0.2 * r["capital"] + 1e-8
                delta = Decimal(str(x["delta"]))
                touch = Decimal(str(x["ask"] if delta > 0 else x["bid"]))
                price = touch * (1 + (1 if delta > 0 else -1) * Decimal(str(x["slip_bps"])) / 10000)
                charge = abs(delta * price) * Decimal(str(x["fee_bps"])) / 10000
                assert abs(price - Decimal(str(x["price"]))) < Decimal("0.00000001")
                assert abs(charge - Decimal(str(x["fee"]))) < Decimal("0.00000001")
                charges += charge
                friction += delta * (price - Decimal(str(x["reference"])))
                q += delta
                if x["signal_ms"] is not None:
                    assert x["time_ms"] > x["signal_ms"] + HOUR
                    assert x["time_ms"] <= x["due_ms"] + 90000
                fills += 1
            s = r["summary"]
            assert abs(charges - Decimal(str(s["fees"]))) < Decimal("0.000001")
            assert abs(friction - Decimal(str(s["friction"]))) < Decimal("0.000001")
            assert abs(q - Decimal(str(s["unclosed_quantity"]))) < Decimal("0.00000001")
            if s["complete"]:
                assert abs(sum(t["net"] for t in r["trades"]) - s["net"]) < 1e-6
                assert abs(s["mid_gross"] - s["friction"] - s["fees"] + s["funding"] - s["net"]) < 1e-6
            records += 1
    print(
        "Verified",
        records,
        "book scenarios;",
        fills,
        "fills: Decimal fees/friction, close accounting and causal timing.",
    )


if __name__ == "__main__":
    checks()
    if (OUT / "results.json").exists():
        verify()
