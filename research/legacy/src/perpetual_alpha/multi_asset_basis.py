"""Per-asset orchestration of existing episode and matched-direction studies.

No orders/network. Valuation is closed by default, independently of precision.
"""
import argparse
import bisect
import gzip
import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .basis_convergence import StudyConfig, distribution, prepare, run
from .basis_direction import metrics
from .basis_direction import run as direction_run
from .historical.contracts import PRICE_UNITS, metadata


def asset_config(symbol, meta, verification, base):
    """Explicit per-market evidence is required; metadata precision is not valuation proof."""
    rules = verification.get(symbol, {})
    supported = (meta["status"] == "ready" and rules.get("verified") is True
                 and rules.get("model") == "linear_quote" and bool(rules.get("evidence_url"))
                 and rules.get("settlement_asset") == "USDPR")
    multiplier = Decimal(str(rules.get("multiplier", "1")))
    if not multiplier.is_finite() or multiplier <= 0:
        raise ValueError("Invalid valuation multiplier")
    minimum = str(rules.get("min_order_value", "10"))
    return replace(base, symbols=(symbol,), quantity_step=meta["quantity_step"] or "1",
                   min_order_value=minimum, pnl_enabled=supported, market_rules_verified=supported), multiplier


def normalize_rows(rows, symbol, multiplier, sz_decimals=None):
    """Convert only a documented linear quote multiplier; no synthetic order books."""
    result, seen, counts = [], set(), Counter()
    previous = None
    for original in sorted((r for r in rows if r["symbol"] == symbol), key=lambda r: r["received_ms"]):
        row = json.loads(json.dumps(original))
        # Preserve full evidence separately; a deterministic last-in-file choice for same-ms receipts.
        if row["received_ms"] in seen:
            result.pop()
            counts["same_receipt_ms_replaced"] += 1
        seen.add(row["received_ms"])
        session = row.get("collection_session")
        if previous is not None and session != previous:
            row["collection_break"] = True
        previous = session
        if row.get("quality") == "invalid":
            counts["collector_invalid"] += 1
        if sz_decimals is not None and row.get("book"):
            for side in row["book"]["levels"]:
                for level in side:
                    price = Decimal(str(level["px"]))
                    if not price.is_finite() or price <= 0:
                        row["execution_rules_invalid"] = True
                        continue
                    normalized = price.normalize()
                    decimals = max(0, -normalized.as_tuple().exponent)
                    if price != price.to_integral_value() and (
                            decimals > 6 - sz_decimals or len(normalized.as_tuple().digits) > 5):
                        row["execution_rules_invalid"] = True
        for key in ("bid", "ask", "oracle", "mark"):
            if key in row:
                row[key] = str(Decimal(str(row[key])) * multiplier)
        book = row.get("book")
        if book:
            for side in book["levels"]:
                for level in side:
                    level["px"] = str(Decimal(str(level["px"])) * multiplier)
        result.append(row)
    return result, dict(counts)


def history_quality(rows, segments, diagnostics):
    valid = [q for segment, _ in segments for q in segment]
    times = [r["received_ms"] for r in rows]
    interval = [b - a for a, b in zip(times, times[1:])]
    duration = (times[-1] - times[0]) / 3600000 if len(times) > 1 else 0
    book_age = [r["received_ms"] - r["event_ms"] for r in rows]
    context_age = [r["received_ms"] - r["context_received_ms"] for r in rows
                   if type(r.get("context_received_ms")) is int]
    # Receipt bins, not unobservable event packet loss. Whole file span includes outages.
    bins = len({t // 5000 for t in times})
    expected = times[-1] // 5000 - times[0] // 5000 + 1 if times else 0
    observed_hours = sum((segment[-1].time_ms - segment[0].time_ms) / 3600000
                         for segment, _ in segments)
    return dict(rows=len(rows), valid_rows=len(valid), diagnostics=diagnostics,
                source_rows=dict(Counter(r.get("collection_session", "unknown") for r in rows)),
                observed_contiguous_hours=observed_hours,
                threshold_sample_fraction={str(t): sum(abs(q.basis) >= t for q in valid) / len(valid)
                                           if valid else None for t in (10, 20, 50)},
                missing_mark_rows=sum("mark" not in r for r in rows),
                bid_levels=distribution([len(r.get("book", {}).get("levels", [[], []])[0]) for r in rows]),
                ask_levels=distribution([len(r.get("book", {}).get("levels", [[], []])[1]) for r in rows]),
                start_ms=times[0] if times else None, end_ms=times[-1] if times else None,
                span_hours=duration, interval_ms=distribution(interval), book_age_ms=distribution(book_age),
                context_age_ms=distribution(context_age),
                repeated_book_event_observations=len(rows) - len({r["event_ms"] for r in rows}),
                stale_book_rows=sum(age < 0 or age > 90000 for age in book_age),
                missing_context_time=len(rows) - len(context_age),
                receipt_5s_bins=bins, expected_5s_bins=expected, missing_5s_bins=expected - bins,
                receipt_5s_coverage=bins / expected if expected else None,
                bid_size=distribution([q.bid_size for q in valid]), ask_size=distribution([q.ask_size for q in valid]),
                oracle_source_freshness="unknown; context receipt does not date upstream reference",
                basis_bps=distribution([q.basis for q in valid]))


def past_regimes(segments):
    """Fixed past-only 5min realized volatility; no fitted/full-sample cutoffs."""
    labels = {}
    for segment, _ in segments:
        times = [q.time_ms for q in segment]
        for i, quote in enumerate(segment):
            left = bisect.bisect_left(times, quote.time_ms - 300000, hi=i)
            past = segment[left:i]  # Excludes the signal quote itself.
            returns = [(b.mid / a.mid - 1) * 10000 for a, b in zip(past, past[1:])]
            # bps per sqrt(minute), adjusted for observed elapsed time, excluding signal quote.
            elapsed_minutes = (past[-1].time_ms - past[0].time_ms) / 60000 if len(past) >= 2 else 0
            rms = math.sqrt(sum(x * x for x in returns) / elapsed_minutes) if elapsed_minutes else None
            enough = len(past) >= 3 and elapsed_minutes >= 4
            labels[(quote.symbol, quote.time_ms)] = ("unknown" if not enough else
                                                     "high_ge_10bps" if rms >= 10 else "low_lt_10bps")
    return labels


def study(rows, metadata_payload, verification=None, config=None, legacy_scenario=False, official_evidence=None):
    meta = metadata(metadata_payload)
    base = config or StudyConfig(bootstrap_repeats=200)
    result = dict(schema="multi-asset-basis/0.1", config=asdict(base), assets={},
                  caveats=["All data exploratory; no out-of-sample claim", "Sampled quotes are not guaranteed fills",
                           "Funding cost is absolute-rate stress, not actual settlement cash",
                           "UTC sessions/weekend flags are not exchange holiday calendars",
                           "Oracle upstream source time unknown; stale reference risk remains"])
    for symbol in PRICE_UNITS:
        cfg, multiplier = asset_config(symbol, meta[symbol], verification or {}, base)
        scenario = legacy_scenario and symbol in ("par:BTC", "par:ETH") and not cfg.pnl_enabled
        if scenario and meta[symbol]["status"] == "ready":
            cfg = replace(cfg, pnl_enabled=True)
            multiplier = Decimal(1)
        batch, preprocessing = normalize_rows(rows, symbol, multiplier, meta[symbol]["sz_decimals"])
        segments, diagnostics = prepare(batch, cfg)
        quality = history_quality(batch, segments, diagnostics)
        item = dict(metadata=meta[symbol], quality=quality, preprocessing=preprocessing,
                    pnl_status=("conditional_legacy_linear_scenario" if scenario else
                                "documented_linear_estimate" if cfg.pnl_enabled else "valuation_unverified"),
                    valuation_evidence=(verification or {}).get(symbol),
                    multiplier_applied=str(multiplier),
                    data_status="insufficient_history", convergence=None, direction=None, stratified=[])
        result["assets"][symbol] = item
        if not any(segment for segment, _ in segments):
            continue
        # Even a short stream is descriptive evidence, not a claim of long-horizon support.
        convergence = run(batch, cfg)
        evidence = ({**official_evidence, "observations": [o for o in official_evidence["observations"]
                     if o["request"].get("coin") == symbol]} if official_evidence else None)
        direction = direction_run(batch, convergence["events"], cfg, evidence)
        labels = past_regimes(segments)
        events = {(e["symbol"], e["trigger_ms"], e["threshold_bps"]): e for e in convergence["events"]
                  if e["band_bps"] == 2}
        for row in direction["observations"]:
            event = events[(row["symbol"], row["trigger_ms"], row["threshold_bps"])]
            row["session_utc"] = event["session_utc"]
            row["past_volatility"] = labels[(row["symbol"], row["trigger_ms"])]
            row["calendar"] = ("weekend" if datetime.fromtimestamp(row["trigger_ms"] / 1000,
                                                                  timezone.utc).weekday() >= 5 else "weekday")
        for field in ("session_utc", "past_volatility", "calendar"):
            for label in sorted({r[field] for r in direction["observations"]}):
                for horizon in cfg.horizons_s:
                    scope = [r for r in direction["observations"] if r[field] == label and r["horizon_s"] == horizon
                             and r["clock"] == "next_quote" and r["threshold_bps"] == 20]
                    item["stratified"].append(dict(field=field, label=label, horizon_s=horizon,
                                                   direction=metrics(scope),
                                                   paired_execution=metrics([r for r in scope if r["retained"]
                                                                             and r["long_eligible"]
                                                                             and r["short_eligible"]])))
        item.update(data_status="exploratory_history" if quality["span_hours"] >= 1 else "short_probe_only",
                    convergence=convergence, direction=direction,
                    event_rates=[dict(threshold_bps=t, band_bps=b,
                                      events=sum(e["threshold_bps"] == t and e["band_bps"] == b
                                                 for e in convergence["events"]),
                                      denominator="wall-clock span including gaps", span_hours=quality["span_hours"],
                                      observed_hours=quality["observed_contiguous_hours"],
                                      events_per_observed_hour=(sum(e["threshold_bps"] == t and e["band_bps"] == b
                                                                   for e in convergence["events"])
                                                                / quality["observed_contiguous_hours"]
                                                                if quality["observed_contiguous_hours"] else None))
                                 for t in cfg.thresholds_bps for b in cfg.bands_bps])
    return result


def file_digest(path):
    raw = path.read_bytes() if path.suffix == ".gz" else path.read_text(encoding="utf-8").encode()
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, nargs="+")
    parser.add_argument("--metadata", type=Path, required=True, help="Raw official meta payload")
    parser.add_argument("--official", type=Path, help="Bounded official funding/recent-trade evidence")
    parser.add_argument("--valuation", type=Path, help="Reviewed per-market linear valuation evidence")
    parser.add_argument("--legacy-btc-eth-scenario", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-repeats", type=int, default=200)
    args = parser.parse_args()
    rows = []
    for path in args.input:
        raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        for line in raw.decode("utf-8-sig").splitlines():
            if line.strip():
                row = json.loads(line)
                row.setdefault("collection_session", path.name)
                rows.append(row)
    result = study(rows, json.loads(args.metadata.read_text(encoding="utf-8-sig")),
                   json.loads(args.valuation.read_text()) if args.valuation else None,
                   StudyConfig(bootstrap_repeats=args.bootstrap_repeats), args.legacy_btc_eth_scenario,
                   json.loads(args.official.read_text()) if args.official else None)
    result["inputs"] = {str(p): file_digest(p)
                        for p in (*args.input, args.metadata, args.valuation, args.official) if p}
    result["hash_convention"] = "gzip bytes; UTF-8 text normalized to LF for text inputs and method sources"
    result["method_sources"] = {p.name: file_digest(p) for p in
                                (Path(__file__), Path(__file__).with_name("basis_convergence.py"),
                                 Path(__file__).with_name("basis_direction.py"))}
    args.output.write_text(json.dumps(result, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({s: a["data_status"] for s, a in result["assets"].items()}))


if __name__ == "__main__":
    main()
