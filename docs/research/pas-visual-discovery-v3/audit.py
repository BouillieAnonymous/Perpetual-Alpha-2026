"""Phase 1: audit frozen public PAS prefix with pinned research normalizer/panel."""

import argparse
import csv
import gzip
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from numeric import pair_qualification, rv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HASH = "fc5ab7a251de06813e9ab74e366d97faee242da96ef0c0f2cd1e722eb861d898"
PREFIX = "62b88879c03aff3cf6b43561409c4bef962704fa3df0e761097df6bc9cfc338b"
PIPE_HASH = "32905565e37c052e95b680c0cd3cd46aa50fbd6c0aab950b126c9dc521539b2c"
SPEC_URL = "https://perpetual-alpha26.github.io/docs/"
GROUPS = {
    "BTC": "Crypto",
    "ETH": "Crypto",
    "HYPE": "Crypto",
    "SP500": "Equity reference",
    "EWY": "Equity reference",
    "EWJ": "Equity reference",
    "BRENT": "Commodity reference",
    "GOLD": "Commodity reference",
    "USDEUR": "FX reference",
    "USDJPY": "FX reference",
}
REFS = {
    "BTC": ("Bitcoin", "USD/BTC"),
    "ETH": ("Ethereum", "USD/ETH"),
    "HYPE": ("HYPE token", "USD/HYPE"),
    "SP500": ("S&P 500 reference", "index points"),
    "EWY": ("iShares MSCI South Korea ETF reference", "USD/share"),
    "EWJ": ("iShares MSCI Japan ETF reference", "USD/share"),
    "BRENT": ("Brent reference", "USD/barrel"),
    "GOLD": ("Gold XAU reference", "USD/troy oz"),
    "USDEUR": ("EUR/USD (ticker is misleading; do not invert)", "USD/EUR"),
    "USDJPY": ("USD/JPY", "JPY/USD"),
}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, obj):
    p.write_bytes(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False).encode())


def utc(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).isoformat()


def quant(a, q):
    a = sorted(v for v in a if v is not None)
    return a[round(q * (len(a) - 1))] if a else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=ROOT / ".cache/validation-2026-10-30/pas-prefix.jsonl.gz")
    ap.add_argument(
        "--pipeline", type=Path, default=ROOT / ".cache/pas-launch-prep/src/perpetual_alpha/pas_research.py"
    )
    ap.add_argument("--output", type=Path, default=HERE / "audit-verified")
    a = ap.parse_args()
    if a.output.exists():
        raise FileExistsError("Preserve evidence: select a new output directory")
    if not a.input.exists():
        raise FileNotFoundError("BLOCKED: frozen all-asset public cache unavailable; no substitute")
    assert sha(a.input) == HASH
    assert (
        hashlib.sha256(a.pipeline.read_text(encoding="utf8").replace("\r\n", "\n").encode()).hexdigest()
        == PIPE_HASH
    )
    sys.path.insert(0, str(a.pipeline.parents[1]))
    from perpetual_alpha import pas_research as pas

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(pas.SCHEMA)
    books = defaultdict(list)
    ranges = defaultdict(list)
    meta = defaultdict(list)
    funds = defaultdict(dict)
    revisions = Counter()
    rawcounts = Counter()
    qualities = defaultdict(Counter)
    prefix = hashlib.sha256()
    last = 0
    payloads = 0
    with gzip.open(a.input, "rt", encoding="utf8") as f:
        header = json.loads(next(f))
        sy = header["identity"]["symbols"]
        assert sy == list(pas.SYMBOLS)
        assert header["identity"] == {
            "schema": "public-venue-collection/1",
            "source": pas.SOURCE,
            "dex": "pas",
            "collateral": "USDP",
            "symbols": sy,
            "endpoint": pas.URL,
        }
        for line in f:
            raw = json.loads(line)
            assert raw["seq"] == last + 1
            last = raw["seq"]
            prefix.update((pas.canonical(raw) + "\n").encode())
            assert hashlib.sha256(raw["raw_json"].encode()).hexdigest() == raw["sha256"]
            payloads += 1
            r, fs = pas.normalize(raw)
            s = r["symbol"]
            rawcounts[(s, raw["kind"])] += 1
            db.execute(
                f"INSERT INTO samples({','.join(r)}) VALUES({','.join('?' for _ in r)})", tuple(r.values())
            )
            if fs:
                db.executemany("INSERT INTO funding VALUES(?,?,?,?,?,?)", fs)
            if s:
                ranges[s].append(r["available_ms"])
                qualities[s][r["kind"] + ":" + r["quality"]] += 1
            if r["kind"] == "book" and s:
                payload = json.loads(raw["raw_json"])
                levels = payload.get("levels", []) if isinstance(payload, dict) else []
                dep = bool(len(levels) == 2 and all(levels))
                b = {
                    k: r[k]
                    for k in [
                        "seq",
                        "available_ms",
                        "received_ms",
                        "event_ms",
                        "quality",
                        "bid",
                        "ask",
                        "mid",
                        "raw_sha256",
                    ]
                }
                b["depth"] = dep
                b["levels_hash"] = hashlib.sha256(pas.canonical(levels).encode()).hexdigest()
                books[s].append(b)
            if raw["kind"] == "metadata-context" and raw["quality"] == "observed":
                payload = json.loads(raw["raw_json"])
                universe = payload[0]["universe"]
                assert {u["name"] for u in universe} == set(sy)
                for u in universe:
                    v = {
                        "available_ms": r["available_ms"],
                        "seq": r["seq"],
                        "szDecimals": u["szDecimals"],
                        "maxLeverage": u["maxLeverage"],
                        "deployerFeeScale": u.get("deployerFeeScale"),
                    }
                    if not meta[u["name"]] or any(
                        meta[u["name"]][-1][k] != v[k]
                        for k in ["szDecimals", "maxLeverage", "deployerFeeScale"]
                    ):
                        meta[u["name"]].append(v)
            for seq, item, symbol, event, available, rate in fs:
                prev = funds[symbol].get(event)
                if prev and prev["last_rate"] != rate:
                    revisions[symbol] += 1
                if not prev:
                    funds[symbol][event] = {
                        "first_available_ms": available,
                        "seq": seq,
                        "first_rate": rate,
                        "last_rate": rate,
                    }
                else:
                    prev["last_rate"] = rate
    assert last == header["max_seq"] == 337657 and prefix.hexdigest() == PREFIX
    db.commit()
    start = min(b["available_ms"] for bs in books.values() for b in bs) // 60000 * 60000
    end = min(max(b["available_ms"] for b in bs) for bs in books.values()) // 60000 * 60000
    times = list(range(start, end + 60000, 60000))
    n = len(times)
    keep = [
        "decision_ms",
        "book_seq",
        "book_event_ms",
        "book_available_ms",
        "book_received_ms",
        "book_stored_ms",
        "book_requested_ms",
        "book_quality",
        "quote_valid",
        "bid",
        "ask",
        "mid",
        "spread_bps",
        "book_event_age_ms",
        "book_request_latency_ms",
        "log_return",
        "context_seq",
        "context_available_ms",
        "context_received_ms",
        "context_age_ms",
        "context_valid",
        "oracle",
        "mark",
        "context_funding",
        "funding_rate",
        "funding_event_ms",
        "funding_available_ms",
        "funding_seq",
    ]
    panel = {s: [] for s in sy}
    for p in pas.panel(db, start, end + 60000):
        panel[p["symbol"]].append({k: p[k] for k in keep})
    db.close()
    checks = 0
    for s in sy:
        bs = sorted(books[s], key=lambda b: (b["available_ms"], b["seq"]))
        j = 0
        prev = None
        prevok = False
        for t, p in zip(times, panel[s]):
            while j < len(bs) and bs[j]["available_ms"] <= t:
                j += 1
            b = bs[j - 1] if j else None
            ok = bool(
                b
                and b["quality"] == "ready"
                and b["event_ms"] is not None
                and 0 <= t - b["event_ms"] <= 90000
                and 0 <= t - b["received_ms"] <= 90000
            )
            ret = None
            strict = False
            if ok and prevok and b["seq"] != prev["seq"] and b["event_ms"] > prev["event_ms"]:
                ret = math.log(b["mid"]) - math.log(prev["mid"])
                strict = (b["bid"], b["ask"]) != (prev["bid"], prev["ask"]) and 30000 <= b["event_ms"] - prev[
                    "event_ms"
                ] <= 90000
            assert (
                p["book_seq"] == (b["seq"] if b else None)
                and p["quote_valid"] == ok
                and p["log_return"] == ret
            )
            assert p["book_available_ms"] is None or p["book_available_ms"] <= t
            assert p["context_available_ms"] is None or p["context_available_ms"] <= t
            assert p["funding_available_ms"] is None or p["funding_available_ms"] <= t
            p["strict_return"] = ret if strict else None
            p["same_event"] = bool(prev and b and b["seq"] == prev["seq"])
            p["unchanged_top"] = bool(ret is not None and (b["bid"], b["ask"]) == (prev["bid"], prev["ask"]))
            p["depth_traceable"] = bool(ok and b["depth"])
            prev, prevok = b, ok
            checks += 1
    a.output.mkdir(parents=True)
    coverage = []
    markets = []
    windows = {}
    components = {}
    for s in sy:
        ps = panel[s]
        bs = books[s]
        alias = s.split(":")[1]
        A = [p["log_return"] for p in ps]
        B = [p["strict_return"] for p in ps]
        valid = [p for p in ps if p["quote_valid"]]
        gaps = []
        run = 0
        for p in ps + [{"quote_valid": True}]:
            if not p["quote_valid"]:
                run += 1
            elif run:
                gaps.append(run)
                run = 0
        duplicate_events = sum(
            x["event_ms"] is not None and x["event_ms"] == y["event_ms"] for x, y in zip(bs, bs[1:])
        )
        reverse_events = sum(
            x["event_ms"] is not None and y["event_ms"] is not None and y["event_ms"] < x["event_ms"]
            for x, y in zip(bs, bs[1:])
        )
        wins = {
            m: {str(w): sum(v is not None for v in rv(r, w)) for w in [5, 15, 30, 60]}
            for m, r in [("A", A), ("B", B)]
        }
        windows[s] = wins
        eventages = [
            p["book_event_age_ms"] / 1000 if p["book_event_age_ms"] is not None else None for p in valid
        ]
        receipt = [(p["decision_ms"] - p["book_received_ms"]) / 1000 for p in valid]
        row = {
            "symbol": s,
            "raw_start_utc": utc(min(ranges[s])),
            "raw_end_utc": utc(max(ranges[s])),
            "book_first_utc": utc(min(b["available_ms"] for b in bs)),
            "book_last_utc": utc(max(b["available_ms"] for b in bs)),
            "grid_points": n,
            "valid_quotes": len(valid),
            "quote_coverage": len(valid) / n,
            "returns_A": sum(v is not None for v in A),
            "coverage_A": sum(v is not None for v in A) / (n - 1),
            "zero_A": sum(v == 0 for v in A),
            "returns_B": sum(v is not None for v in B),
            "coverage_B": sum(v is not None for v in B) / (n - 1),
            "zero_B": sum(v == 0 for v in B),
            "duplicate_raw_event_n": duplicate_events,
            "backward_raw_event_n": reverse_events,
            "reused_grid_event_n": sum(p["same_event"] for p in ps),
            "unchanged_new_top_n": sum(p["unchanged_top"] for p in ps),
            "identical_payload_n": sum(x["raw_sha256"] == y["raw_sha256"] for x, y in zip(bs, bs[1:])),
            "identical_levels_n": sum(x["levels_hash"] == y["levels_hash"] for x, y in zip(bs, bs[1:])),
            "missing_quotes": n - len(valid),
            "longest_missing_grid_min": max(gaps, default=0),
            "spread_median_bps": quant([p["spread_bps"] for p in valid], 0.5),
            "spread_p99_bps": quant([p["spread_bps"] for p in valid], 0.99),
            "valid_event_age_median_sec": quant(eventages, 0.5),
            "valid_event_age_p99_sec": quant(eventages, 0.99),
            "valid_receipt_age_p99_sec": quant(receipt, 0.99),
            "book_request_latency_p99_ms": quant([p["book_request_latency_ms"] for p in ps], 0.99),
        }
        for m, ws in wins.items():
            for w, count in ws.items():
                row[f"complete_{w}m_{m}"] = count
        for label, flag in [
            ("oracle", lambda p: p["context_valid"] and p["oracle"] is not None),
            ("settled_funding", lambda p: p["funding_rate"] is not None),
            ("depth", lambda p: p["depth_traceable"]),
        ]:
            row[label + "_grid_valid"] = sum(flag(p) for p in ps)
            row[label + "_grid_coverage"] = row[label + "_grid_valid"] / n
        row["unique_funding_events"] = len(funds[s])
        row["funding_rate_revision_n"] = revisions[s]
        row["funding_receipts"] = rawcounts[(s, "settled-funding")]
        row["raw_book_requests"] = len(bs)
        row["visual_status"] = (
            "VISUAL_READY"
            if len(valid) >= 60 and row["returns_A"] >= 60 and end - start >= 86400000
            else "INSUFFICIENT_DATA"
        )
        coverage.append(row)
        markets.append(
            {
                "symbol": s,
                "id": alias,
                "group": GROUPS[alias],
                "reference": REFS[alias][0],
                "price_unit": REFS[alias][1],
                "metadata_versions": meta[s],
                "lot_sizes": sorted({10 ** (-v["szDecimals"]) for v in meta[s]}),
                "official_sessions": "PAS 24/7 (current guide); underlying session differs/unknown historically",
                "underlying_session_historical": "UNKNOWN — no point-in-time exchange calendar supplied",
                "official_spec_url": SPEC_URL,
                "official_spec_checked": "2026-11-05",
                "official_spec_not_historical_execution_proof": True,
                "actual_fee_bps_side": None,
                "actual_slippage_bps": None,
                "historical_min_notional": None,
                "current_guide_min_notional": 10,
                "visual_status": row["visual_status"],
                "coverage": row,
            }
        )
        components[s] = {
            "normalized_qualities": dict(qualities[s]),
            "funding": {
                "unique_events": len(funds[s]),
                "revisions": revisions[s],
                "event_first_utc": utc(min(funds[s])) if funds[s] else None,
                "event_last_utc": utc(max(funds[s])) if funds[s] else None,
                "first_availability_records": {str(k): v for k, v in funds[s].items()},
            },
            "oracle_upstream_event_time": "UNKNOWN",
            "depth": "exact raw l2Book snapshot with payload hash; not continuous queue or fill proof",
        }
    pairrows = []
    for ix, x in enumerate(sy):
        for y in sy[ix + 1 :]:
            for m, key in [("A", "log_return"), ("B", "strict_return")]:
                r = pair_qualification([p[key] for p in panel[x]], [p[key] for p in panel[y]], times)
                r = {
                    "x": x,
                    "y": y,
                    "mode": m,
                    "joint_quotes": sum(
                        px["quote_valid"] and py["quote_valid"] for px, py in zip(panel[x], panel[y])
                    ),
                    "grid_points": n,
                    **r,
                }
                pairrows.append(r)
    for name, rows in [("coverage.csv", coverage), ("pair-eligibility.csv", pairrows)]:
        with (a.output / name).open("w", newline="", encoding="utf8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    # Preserve audit panel clocks and lineage for independent verification; no raw secret data involved.
    with gzip.GzipFile(filename=str(a.output / "causal-panel.jsonl.gz"), mode="wb", mtime=0) as f:
        for s in sy:
            for p in panel[s]:
                f.write((pas.canonical({"symbol": s, **p}) + "\n").encode())
    metadata = {
        "source": pas.SOURCE,
        "start_ms": start,
        "end_ms": end,
        "start_utc": utc(start),
        "end_utc": utc(end),
        "n": n,
        "max_seq": last,
        "input_sha256": HASH,
        "canonical_prefix_sha256": PREFIX,
        "pipeline_lf_sha256": PIPE_HASH,
        "markets": markets,
        "pair_eligibility": pairrows,
        "official_spec": {
            "url": SPEC_URL,
            "checked_utc_date": "2026-11-05",
            "scope": "current published reference/24x7/precision rules; historical actual fees, fills and sessions not inferred",
        },
        "validation": {
            "raw_payloads": payloads,
            "independent_asset_minutes": checks,
            "source_identity": "PAS not PAR",
            "asof_components_le_decision": True,
        },
    }
    write(a.output / "metadata.json", metadata)
    write(a.output / "component-audit.json", components)
    # Known earlier 10-asset EDA counts, not its unverified UI state.
    old = HERE / "prior-ten-asset-counts.json"
    cross = 0
    if old.exists():
        earlier = json.loads(old.read_text(encoding="utf8"))
        assert earlier["input_sha256"] == HASH and earlier["grid_points"] == n
        differences = []
        for r in coverage:
            q = earlier["quality"][r["symbol"]]
            for k, new in [
                ("valid_quotes", "valid_quotes"),
                ("valid_returns_A", "returns_A"),
                ("valid_returns_B", "returns_B"),
                ("zero_returns_A", "zero_A"),
            ]:
                if q[k] != r[new]:
                    assert (
                        k == "valid_quotes"
                        and r["symbol"] in {"pas:USDEUR", "pas:USDJPY"}
                        and q[k] - r[new] == 1
                    )
                    differences.append(
                        {
                            "symbol": r["symbol"],
                            "field": k,
                            "old": q[k],
                            "v3": r[new],
                            "reason": "Earlier book-only loader skipped request-error normalized as latest invalid book; V3 follows pinned full panel.",
                        }
                    )
                else:
                    cross += 1
        write(a.output / "prior-loader-differences.json", differences)
    write(
        a.output / "validation.json",
        {
            "status": "PASS",
            "payload_hashes": payloads,
            "independent_minutes": checks,
            "prior_tenasset_counts": cross,
            "future_truncation": "PENDING_INDEPENDENT_VERIFIER",
            "source_sha256": HASH,
        },
    )
    lines = [
        "# PAS All-Asset Data Audit V3",
        "",
        f"固定真实PAS公共前缀：{utc(start)}—{utc(end)}，{n} UTC分钟点，max_seq={last}。不是BTC/ETH的11月3日数据；不拼接、不新采集。",
        "",
        f"输入SHA256 `{HASH}`；canonical prefix `{PREFIX}`；全部{payloads}个payload hash核对。复用原normalize/panel并独立到达重建{checks}资产分钟点。",
        "",
        "## 十资产逐项覆盖",
        "",
        "|市场|报价覆盖|A收益|B收益|A零收益|缺报价|最长缺口min|spread中位bps|Oracle覆盖|Funding覆盖|Depth覆盖|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in coverage:
        lines.append(
            f"|{r['symbol']}|{r['quote_coverage']:.3%}|{r['coverage_A']:.3%}|{r['coverage_B']:.3%}|{r['zero_A']}|{r['missing_quotes']}|{r['longest_missing_grid_min']}|{r['spread_median_bps']:.3f}|{r['oracle_grid_coverage']:.3%}|{r['settled_funding_grid_coverage']:.3%}|{r['depth_grid_coverage']:.3%}|"
        )
    lines += [
        "",
        "旧十资产book-only EDA漏掉两条归类为book的请求失败。V3按完整normalizer使USDEUR/USDJPY各减少1个有效报价点；A/B收益计数不变。差异及原因见prior-loader-differences.json，原证据未覆盖。全量字段见coverage.csv：各市场原始/盘口起止、重复事件、回退、同事件复用、静止顶层、完整5/15/30/60m窗、spread分位、event/receipt年龄及延迟。A有效新事件的真实零收益保留；B是活跃样本选择，不能解释为全时段波动修正。缺失切断窗，不补零或插值。",
        "",
        "## 所有市场对资格",
        "",
        f"45对分别报告A/B，共90行。A数据协议规划资格通过 {sum(r['status'] == 'DATA_READY_FOR_PROTOCOL' for r in pairrows if r['mode'] == 'A')}/45，B通过 {sum(r['status'] == 'DATA_READY_FOR_PROTOCOL' for r in pairrows if r['mode'] == 'B')}/45。数据门槛不是独立样本数、统计功效或可交易性证明。配对不足仍展示名称和INSUFFICIENT_DATA。详见pair-eligibility.csv与固定protocol。",
        "",
        "## 合约与时段语义",
        "",
        "[官方参与指南](https://perpetual-alpha26.github.io/docs/#asset-references-and-pricing) 于2026-11-05查阅：PAS合约参考加密、股指/ETF、商品和FX；PAS报价为24/7。USDEUR是EUR/USD，不倒置。实际PAS盘口不等于底层ETF、指数或现货成交。发布的参考标的与统一Oracle机制不能证明独立价格发现或历史可交易时差。",
        "",
        "|市场|分组|参考/价格单位|历史缓存 lot|",
        "|---|---|---|---|",
    ]
    for m in markets:
        lines.append(f"|{m['symbol']}|{m['group']}|{m['reference']} / {m['price_unit']}|{m['lot_sizes']}|")
    lines += [
        "",
        "metadata_versions保留当时实际可得序号和时标；当前官方规则仅补充语义，不证明历史每单实际约束。实际账户费率、成交、滑点与历史最小名义UNKNOWN，不沿用BTC/ETH预算或精度。页面默认仅展示市场自身spread；费用/滑点需逐市场显式输入假设。底层交易所历史时段/假日/DST未提供，不用UTC小时伪装交易日历。",
        "",
        "## Funding、Oracle、Depth",
        "",
        "Oracle/context有独立receipt和TTL，但无上游生成时间；不能从报价相同时间推出同步。Funding按真实settlement event及首次available溯源，重复receipt不算新结算；修订单列。Funding覆盖为管线2h TTL内已知settled rate，不是账户现金流。完整raw L2可逐seq溯源覆盖，但仅分钟异步快照，不是逐笔队列/可成交深度保证；没有秒级盘口图。component-audit.json保留首可得Funding事件与各类原始质量计数。",
        "",
        "## 已见样本与停止",
        "",
        "只有约11天历史，未覆盖BTC/ETH版剩余尾段，无新留出。45对共享市场和冲击，不是45次独立实验。本轮不做新假设检验、参数搜索、回测或模型训练。历史可视化不推翻PR15/20/27/38的失败与执行未知结论。",
    ]
    (a.output / "all-asset-data-audit.md").write_bytes("\n".join(lines).encode())
    write(a.output / "manifest.json", {p.name: sha(p) for p in a.output.iterdir() if p.is_file()})
    print(
        json.dumps(
            metadata["validation"]
            | {
                "grid": n,
                "A_pairs_ready": sum(
                    r["status"] == "DATA_READY_FOR_PROTOCOL" for r in pairrows if r["mode"] == "A"
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
