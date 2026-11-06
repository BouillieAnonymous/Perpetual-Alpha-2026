"""Original PR15 17 rules on frozen PAS sampled hours and observed books; offline only."""

import csv
import gzip
import hashlib
import json
import math
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

from study_pas_weekly_alpha import CUT1, CUT2, HOUR, MINUTE, canonical, load, past, utc

OUT = Path("docs/research/weekly-synthesis-2026-10-26/traditional17")
sys.path.insert(0, str(OUT.resolve()))
from frozen17.data import Bar  # noqa: E402
from frozen17.relative import funding_orders  # noqa: E402
from frozen17.replay import Costs, quantity, replay  # noqa: E402
from frozen17.run import orders_for  # noqa: E402
from frozen17.signals import Order  # noqa: E402


def sampled_hours(symbol, quotes):
    buckets = defaultdict(list)
    first = (quotes[0]["t"] // HOUR + 1) * HOUR
    stop = quotes[-1]["t"] // HOUR * HOUR
    for q in quotes:
        if first <= q["t"] < stop:
            buckets[q["t"] // HOUR * HOUR].append(q)
    bars, quality = [], []
    for t in range(first, stop, HOUR):
        qs = buckets[t]
        unique = len({q["t"] // MINUTE for q in qs})
        gap = max((b["t"] - a["t"] for a, b in zip(qs, qs[1:])), default=0)
        good = bool(
            qs
            and unique >= 54
            and qs[0]["t"] - t <= 90000
            and t + HOUR - qs[-1]["t"] <= 90000
            and gap <= 180000
        )
        quality.append(
            dict(
                symbol=symbol,
                close_ms=t + HOUR,
                samples=len(qs),
                unique_minutes=unique,
                max_internal_gap_ms=gap,
                accepted=good,
            )
        )
        if good:
            prices = [q["mid"] for q in qs]
            bars.append(Bar(symbol, t + HOUR, prices[0], max(prices), min(prices), prices[-1], 0.0, t + HOUR))
    return bars, quality


def generate(definition, rows, funds, start, end):
    symbol = rows[0].symbol
    d = {**definition, "symbols": [symbol]}
    truncated = [b for b in rows if b.time < end]
    if d["kind"] != "funding":
        return orders_for(d, {symbol: truncated}, {}, {}, start, end)
    orders = []
    for b in truncated:
        if b.time < start or b.time // HOUR % 12:
            continue
        # Preserve the original Funding rule; supply only received settlement vintages.
        known = {}
        for f in funds:
            if f["t"] <= b.time:
                known.setdefault(f["event"], f["rate"])
        orders.extend(funding_orders({symbol: [b]}, {symbol: known}, [symbol], mode=d["mode"]))
    return orders


def book_replay(symbol, quotes, contexts, settlements, orders, start, end, capital, fee=9, slip=2):
    qs = [q for q in quotes if start <= q["t"] < end]
    if not qs:
        raise ValueError("Empty book replay")
    qt = [q["t"] for q in quotes]
    ct = [c["t"] for c in contexts]
    planned = []
    for o in orders:
        if start <= o.time < end and o.time + HOUR < end:
            due = o.time + HOUR
            j = bisect_right(qt, due) - 1
            old_event = quotes[j]["event"] if j >= 0 else -1
            planned.append(
                dict(
                    signal=o.time,
                    due=due,
                    expires=due + 90000,
                    old_event=old_event,
                    weight=o.targets[symbol],
                    reason=o.reason,
                )
            )
    planned.sort(key=lambda x: x["due"])
    fs = [f for f in settlements if start < f["event"] < end]
    observed_funding_hours = {f["event"] // HOUR for f in settlements}
    oi = fi = 0
    pending = None
    pos = dict(q=0.0, avg=0.0, avg_ref=0.0, fee=0.0, funding=0.0, opened=start)
    cash = capital
    fees = friction = fund_cash = gross_realized = turnover = 0.0
    fills, trades, funding_rows, equity = [], [], [], []
    rejected, unknown_funding, peak, maxdd, stale_intervals = Counter(), 0, capital, 0.0, 0
    previous_time = None

    def fill(q, target, reason, plan=None, terminal=False):
        nonlocal cash, fees, friction, gross_realized, turnover, pos
        old = pos.copy()
        q0, delta = old["q"], target - old["q"]
        if abs(delta) < 1e-12:
            return
        if abs(delta * q["mid"]) < 10:
            rejected["minimum_notional"] += 1
            return
        if abs(delta) > q["az" if delta > 0 else "bs"] + 1e-12:
            rejected["terminal_depth" if terminal else "depth"] += 1
            return
        touch = q["ask"] if delta > 0 else q["bid"]
        execution = touch * (1 + math.copysign(slip / 10000, delta))
        charge = abs(delta * execution) * fee / 10000
        cost = delta * (execution - q["mid"])
        fees += charge
        friction += cost
        turnover += abs(delta * q["mid"])
        cash -= charge
        remaining_fee = charge
        if q0 * delta < 0:
            closed = min(abs(q0), abs(delta))
            direction = math.copysign(1, q0)
            realized = direction * closed * (execution - old["avg"])
            gross = direction * closed * (q["mid"] - old["avg_ref"])
            cash += realized
            gross_realized += gross
            allocated_fee = old["fee"] * closed / abs(q0)
            allocated_fund = old["funding"] * closed / abs(q0)
            exit_fee = charge * closed / abs(delta)
            trades.append(
                dict(
                    entry_ms=old["opened"],
                    exit_ms=q["t"],
                    quantity=closed,
                    direction=direction,
                    gross=gross,
                    price_after_friction=realized,
                    fees=allocated_fee + exit_fee,
                    funding=allocated_fund,
                    net=realized - allocated_fee - exit_fee + allocated_fund,
                    reason=reason,
                )
            )
            old.update(fee=old["fee"] - allocated_fee, funding=old["funding"] - allocated_fund)
            remaining_fee -= exit_fee
        if target == 0:
            pos = dict(q=0.0, avg=0.0, avg_ref=0.0, fee=0.0, funding=0.0, opened=q["t"])
        elif q0 * target <= 0:
            pos = dict(
                q=target, avg=execution, avg_ref=q["mid"], fee=remaining_fee, funding=0.0, opened=q["t"]
            )
        elif abs(target) > abs(q0):
            added = abs(target) - abs(q0)
            pos = {
                **old,
                "q": target,
                "avg": (abs(q0) * old["avg"] + added * execution) / abs(target),
                "avg_ref": (abs(q0) * old["avg_ref"] + added * q["mid"]) / abs(target),
                "fee": old["fee"] + remaining_fee,
            }
        else:
            pos = {**old, "q": target}
        fills.append(
            dict(
                time_ms=q["t"],
                seq=q["seq"],
                delta=delta,
                target=target,
                reference=q["mid"],
                bid=q["bid"],
                ask=q["ask"],
                price=execution,
                fee=charge,
                friction=cost,
                slip_bps=slip,
                fee_bps=fee,
                reason=reason,
                signal_ms=plan["signal"] if plan else None,
                due_ms=plan["due"] if plan else None,
            )
        )

    for i, q in enumerate(qs):
        if pos["q"] and previous_time is not None:
            for hour in range(previous_time // HOUR, q["t"] // HOUR + 1):
                if previous_time < hour * HOUR + 1000 <= q["t"] and hour not in observed_funding_hours:
                    unknown_funding += 1
        while fi < len(fs) and fs[fi]["event"] <= q["t"]:
            f = fs[fi]
            if pos["q"]:
                c = past(contexts, ct, f["event"])
                if c:
                    amount = -pos["q"] * c["oracle"] * f["rate"]
                    cash += amount
                    fund_cash += amount
                    pos["funding"] += amount
                    funding_rows.append(
                        dict(
                            event=f["event"],
                            quantity=pos["q"],
                            oracle=c["oracle"],
                            rate=f["rate"],
                            amount=amount,
                            oracle_seq=c["seq"],
                        )
                    )
                else:
                    unknown_funding += 1
            fi += 1
        if pos["q"] and previous_time and q["t"] - previous_time > 90000:
            stale_intervals += 1
        previous_time = q["t"]
        while oi < len(planned) and planned[oi]["due"] < q["t"]:
            if pending:
                rejected["superseded_or_expired"] += 1
            pending = planned[oi]
            oi += 1
        if pending:
            if q["t"] > pending["expires"]:
                rejected["expired"] += 1
                pending = None
            elif q["event"] > pending["old_event"]:
                if past(contexts, ct, q["t"]) is None:
                    rejected["context_stale"] += 1
                else:
                    target = quantity(pending["weight"] * capital, q["mid"], 10 ** -q["decimals"])
                    fill(q, target, pending["reason"], pending)
                pending = None
        if i == len(qs) - 1 and pos["q"]:
            fill(q, 0.0, "terminal-liquidation-estimate", terminal=True)
        value = cash + pos["q"] * (q["mid"] - pos["avg"])
        peak = max(peak, value)
        maxdd = max(maxdd, (peak - value) / peak)
        # Minute mark-to-mid, not the competition's official mark equity.
        equity.append(dict(time=q["t"], value=value, gross=abs(pos["q"] * q["mid"]) / capital))
    known = cash - capital + pos["q"] * (qs[-1]["mid"] - pos["avg"])
    complete = pos["q"] == 0 and unknown_funding == 0
    if complete:
        assert abs(known - (gross_realized - friction - fees + fund_cash)) < 1e-6
        assert abs(sum(t["net"] for t in trades) - known) < 1e-6
    daily = {}
    for e in equity:
        daily[utc(e["time"])[:10]] = e["value"] - capital
    summary = dict(
        capital=capital,
        signals=len([o for o in orders if start <= o.time < end]),
        entry_signals=sum(any(o.targets.values()) for o in orders if start <= o.time < end),
        fills=len(fills),
        closed_segments=len(trades),
        mid_gross=gross_realized if complete else None,
        friction=friction,
        fees=fees,
        funding=fund_cash,
        net=known if complete else None,
        known_components_net=known,
        net_return=known / capital if complete else None,
        max_drawdown=maxdd,
        mean_gross_fraction=sum(e["gross"] for e in equity) / len(equity),
        rejects=dict(rejected),
        unclosed_quantity=pos["q"],
        funding_unknown=unknown_funding,
        stale_mark_intervals=stale_intervals,
        complete=complete,
        daily_cumulative_pnl=daily,
        turnover=turnover,
        fee_bps=fee,
        slip_bps=slip,
    )
    return dict(summary=summary, fills=fills, trades=trades, funding=funding_rows, equity=equity)


def main():
    output = OUT
    if (output / "results.json").exists():
        raise FileExistsError("Preserve existing study results")
    books, contexts, funds, settlements, manifest = load(
        Path(".cache/alpha-week-2026-10-26/pas-prefix.jsonl.gz")
    )
    bars, quality = {}, []
    for s, qs in books.items():
        bars[s], rows = sampled_hours(s, qs)
        quality.extend(rows)
    start = max(v[0].time for v in bars.values())
    end = min(v[-1].time for v in bars.values())
    splits = [
        ("full", start, end),
        ("development", start, CUT1),
        ("validation", CUT1, CUT2),
        ("audit", CUT2, end),
    ]
    defs = json.loads((output / "definitions.json").read_text())
    summaries = []
    signals = []
    hourly = []
    detail_path = output / "book-ledgers.jsonl.gz"
    with gzip.GzipFile(filename=str(detail_path), mode="wb", mtime=0) as detail:
        for s in sorted(bars):
            rates = {s: {f["event"]: f["rate"] for f in settlements[s]}}
            specs = {s: {"quantity_step": 10 ** -manifest["specs"][s]}}
            definitions = defs + [
                dict(id="FIXED-LONG", kind="fixed", side=1),
                dict(id="FIXED-SHORT", kind="fixed", side=-1),
                dict(id="NO-TRADE", kind="fixed", side=0),
            ]
            for d in definitions:
                for split, a, b in splits:
                    if d["kind"] == "fixed":
                        orders = (
                            [
                                Order(r.time, {s: d["side"] * 0.2}, "fixed-direction")
                                for r in bars[s]
                                if a <= r.time < b and r.time // HOUR % 12 == 0
                            ]
                            if d["side"]
                            else []
                        )
                    else:
                        orders = generate(d, bars[s], funds[s], a, b)
                    valid = [o for o in orders if a <= o.time < b]
                    identity = dict(symbol=s, rule=d["id"], split=split)
                    signals.append(dict(**identity, orders=[asdict(o) for o in valid]))
                    if s == "pas:USDJPY":
                        summaries.append(
                            dict(
                                **identity,
                                mode="valuation-blocked",
                                capital=None,
                                scenario="not-valued",
                                summary=dict(
                                    signals=len(valid),
                                    entry_signals=sum(any(o.targets.values()) for o in valid),
                                    net=None,
                                ),
                            )
                        )
                        continue
                    for scenario, cost in [
                        ("base", Costs()),
                        ("stress", Costs(fee_bps=18, slippage_bps=4, spread_bps=18, delay_hours=2)),
                    ]:
                        result = replay({s: bars[s]}, rates, specs, orders, a, b, cost)
                        hourly.append(dict(**identity, scenario=scenario, summary=result["summary"]))
                    for capital in (10000, 500):
                        for scenario, fee, slip in [("base", 9, 2), ("stress", 18, 4)]:
                            result = book_replay(
                                s, books[s], contexts[s], settlements[s], orders, a, b, capital, fee, slip
                            )
                            row = dict(
                                **identity,
                                mode="observed-book",
                                capital=capital,
                                scenario=scenario,
                                summary=result["summary"],
                            )
                            summaries.append(row)
                            detail.write(
                                (
                                    canonical(dict(**identity, capital=capital, scenario=scenario, **result))
                                    + "\n"
                                ).encode()
                            )
                print(s, d["id"], "done", flush=True)
    sources = [
        Path(__file__),
        Path("docs/research/weekly-synthesis-2026-10-26/traditional17-protocol.zh-CN.md"),
    ]
    result = dict(
        schema="pas-original-traditional17/1",
        manifest=manifest,
        start_utc=utc(start),
        end_utc=utc(end),
        data_kind="sampled hourly mid OHLC; not official trade candles",
        hour_quality=quality,
        definitions=defs,
        source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        bar_counts={s: len(v) for s, v in bars.items()},
        book_results=summaries,
        hourly_results=hourly,
    )
    (output / "results.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    with gzip.GzipFile(filename=str(output / "signals.jsonl.gz"), mode="wb", mtime=0) as f:
        for row in signals:
            f.write((canonical(row) + "\n").encode())
    (output / "sampled-bars.json").write_text(
        json.dumps({s: [asdict(r) for r in rows] for s, rows in bars.items()}, indent=2) + "\n",
        encoding="utf-8",
    )
    fields = [
        "symbol",
        "rule",
        "split",
        "mode",
        "capital",
        "scenario",
        "signals",
        "entry_signals",
        "fills",
        "closed_segments",
        "net",
        "net_return",
        "mid_gross",
        "fees",
        "friction",
        "funding",
        "complete",
        "max_drawdown",
        "unclosed_quantity",
        "funding_unknown",
        "rejects",
    ]
    with (output / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore")
        w.writeheader()
        for row in summaries:
            w.writerow({**{k: v for k, v in row.items() if k != "summary"}, **row["summary"]})
    print(
        canonical(
            dict(
                start=utc(start),
                end=utc(end),
                bars=result["bar_counts"],
                book_scenarios=len(summaries),
                hour_scenarios=len(hourly),
            )
        )
    )


if __name__ == "__main__":
    main()
