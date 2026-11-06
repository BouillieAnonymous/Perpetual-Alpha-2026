"""All-positive post-selection direction controls and report tables; no rule tuning."""

import gzip
import hashlib
import json
from pathlib import Path

from study_pas_traditional17 import OUT, Order, book_replay, load


def main():
    r = json.loads((OUT / "results.json").read_text())
    ids = {d["id"] for d in r["definitions"]}
    original = [
        x
        for x in r["book_results"]
        if x["mode"] == "observed-book"
        and x["capital"] == 500
        and x["split"] == "full"
        and x["scenario"] == "base"
        and x["rule"] in ids
    ]
    positive = [x for x in original if x["summary"]["net"] is not None and x["summary"]["net"] > 0]
    signal_map = {}
    with gzip.open(OUT / "signals.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            x = json.loads(line)
            if x["split"] == "full":
                signal_map[x["symbol"], x["rule"]] = x["orders"]
    books, contexts, _, settlements, _ = load(Path(".cache/alpha-week-2026-10-26/pas-prefix.jsonl.gz"))
    start = min(q["time_ms"] for q in next(iter(signal_map.values()))) if False else None
    from datetime import datetime

    start = int(datetime.fromisoformat(r["start_utc"]).timestamp() * 1000)
    end = int(datetime.fromisoformat(r["end_utc"]).timestamp() * 1000)
    controls = []
    for x in positive:
        s, k = x["symbol"], x["rule"]
        row = dict(symbol=s, rule=k, strategy=x["summary"], controls={})
        for direction in (1, -1):
            orders = [
                Order(o["time"], {s: direction * abs(o["targets"][s])}, "matched-direction", o["features"])
                for o in signal_map[s, k]
            ]
            result = book_replay(s, books[s], contexts[s], settlements[s], orders, start, end, 500)
            row["controls"][str(direction)] = result["summary"]
        known = [v["net"] for v in row["controls"].values()]
        row["excess_over_better_direction"] = x["summary"]["net"] - max(known) if None not in known else None
        controls.append(row)
    out = dict(
        schema="pas17-post-selection-controls/1",
        note="All 41 full/base/capital500 positive rows, including duplicates/random; retrospective diagnostics, not independent confirmation",
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        controls=controls,
    )
    (OUT / "direction-controls.json").write_text(
        json.dumps(out, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    for x in sorted(
        controls,
        key=lambda x: (
            x["excess_over_better_direction"] if x["excess_over_better_direction"] is not None else -1e99
        ),
        reverse=True,
    ):
        print(
            x["symbol"],
            x["rule"],
            "net",
            round(x["strategy"]["net"], 5),
            "matched",
            [round(c["net"], 5) if c["net"] is not None else None for c in x["controls"].values()],
            "excess",
            round(x["excess_over_better_direction"], 5)
            if x["excess_over_better_direction"] is not None
            else None,
        )


if __name__ == "__main__":
    main()
