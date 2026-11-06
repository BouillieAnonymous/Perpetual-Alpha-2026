"""Frozen, offline PAS Alpha screen. Standard library only; no network/execution imports."""
import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

MINUTE = 60000
HOUR = 60 * MINUTE
RULES = ('mom5', 'mom60', 'mom240', 'mr60', 'basis20', 'basis50',
         'funding_last', 'funding_context', 'imbalance', 'oracle5', 'btc_lead', 'breakout60')
HOLDS = (30, 60, 240)
CUT1 = int(datetime(2026, 10, 23, tzinfo=timezone.utc).timestamp() * 1000)
CUT2 = int(datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp() * 1000)


def utc(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).isoformat()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False)


def number(x):
    y = float(x)
    if not math.isfinite(y):
        raise ValueError('Nonfinite input')
    return y


def load(path):
    books, contexts, funding, specs = defaultdict(list), defaultdict(list), defaultdict(list), {}
    counts = Counter()
    prefix = hashlib.sha256()
    previous_seq = 0
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        header = json.loads(next(f))
        assert header['schema'] == 'pas-alpha-prefix/1'
        assert header['identity']['dex'] == 'pas' and header['identity']['collateral'] == 'USDP'
        for line in f:
            r = json.loads(line)
            assert r['seq'] > previous_seq
            previous_seq = r['seq']
            assert hashlib.sha256(r['raw_json'].encode()).hexdigest() == r['sha256']
            prefix.update(canonical(r).encode() + b'\n')
            counts[r['kind']] += 1
            t = max(r[k] for k in ('requested_ms', 'received_ms', 'stored_ms'))
            raw, s = json.loads(r['raw_json']), r['symbol']
            try:
                if r['kind'] == 'metadata-context':
                    for x in raw[0]['universe']:
                        if x['name'] in specs:
                            assert specs[x['name']] == x['szDecimals'], 'Changing lot precision'
                        specs[x['name']] = x['szDecimals']
                elif r['kind'] == 'book' and r['quality'] == 'ready':
                    assert raw['coin'] == s and raw['time'] == r['event_ms']
                    if not 0 <= t - raw['time'] <= 90000:
                        counts['bad_book_age'] += 1
                        continue
                    b, a = raw['levels']
                    bid, ask = number(b[0]['px']), number(a[0]['px'])
                    bs, az = number(b[0]['sz']), number(a[0]['sz'])
                    assert 0 < bid <= ask and min(bs, az) > 0
                    b5, a5 = sum(number(x['sz']) for x in b[:5]), sum(number(x['sz']) for x in a[:5])
                    books[s].append(dict(t=t, event=raw['time'], bid=bid, ask=ask,
                                         bs=bs, az=az, mid=(bid + ask) / 2, seq=r['seq'],
                                         imbalance=(b5 - a5) / (b5 + a5), decimals=specs[s]))
                elif r['kind'] == 'context' and r['quality'] == 'observed':
                    ctx = raw['context']
                    oracle = number(ctx['oraclePx'])
                    assert oracle > 0
                    contexts[s].append(dict(t=t, oracle=oracle, funding=number(ctx['funding']), seq=r['seq']))
                elif r['kind'] == 'settled-funding' and r['quality'] == 'observed':
                    for x in raw:
                        assert x['coin'] == s and x['time'] <= t
                        funding[s].append(dict(t=t, event=x['time'], rate=number(x['fundingRate']), seq=r['seq']))
            except (ValueError, KeyError, IndexError, ZeroDivisionError):
                counts['parse_rejected_' + r['kind']] += 1
    assert previous_seq == header['max_seq']
    for group in (books, contexts, funding):
        for v in group.values():
            v.sort(key=lambda x: (x['t'], x['seq']))
    first_funds = {}
    for s, rows in funding.items():
        by_time = {}
        for x in rows:
            if x['event'] in by_time and x['rate'] != by_time[x['event']]['rate']:
                counts['funding_revision_receipts'] += 1
            by_time.setdefault(x['event'], x)
        first_funds[s] = sorted(by_time.values(), key=lambda x: x['event'])
    manifest = dict(header=header, raw_counts=dict(counts), prefix_sha256=prefix.hexdigest(),
                    input_gzip_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), specs=specs,
                    independent_funding_events={s: len(v) for s, v in first_funds.items()})
    return books, contexts, funding, first_funds, manifest


def past(rows, times, t, ttl=90000):
    i = bisect_right(times, t) - 1
    if i < 0 or t - rows[i]['t'] > ttl:
        return None
    x = rows[i]
    if 'event' in x and not 0 <= t - x['event'] <= ttl:
        return None
    return x


def panel(books, contexts, funds, start=None, end=None):
    start = start if start is not None else (min(v[0]['t'] for v in books.values()) // MINUTE + 1) * MINUTE
    end = end if end is not None else min(v[-1]['t'] for v in books.values()) // MINUTE * MINUTE
    grids, quality = {}, {}
    for s, qs in sorted(books.items()):
        qt, cs = [q['t'] for q in qs], contexts[s]
        ct, fs = [c['t'] for c in cs], funds[s]
        ft = [f['t'] for f in fs]
        samples, missing = [], 0
        for t in range(start, end + 1, MINUTE):
            q, c = past(qs, qt, t), past(cs, ct, t)
            if not q or not c:
                samples.append(None)
                missing += 1
                continue
            fidx = bisect_right(ft, t)
            known = fs[:fidx]
            # Most recently settled event that had actually arrived at this decision time.
            last = max(known[-8:], key=lambda x: (x['event'], -x['t']), default=None)
            last_rate = last['rate'] if last and 0 <= t - last['event'] <= 2 * HOUR else None
            p = dict(t=t, q=q, oracle=c['oracle'], context_seq=c['seq'],
                     funding_context=c['funding'], funding_last=last_rate,
                     basis=(q['mid'] / c['oracle'] - 1) * 10000,
                     imbalance=q['imbalance'])
            for lag in (5, 60, 240):
                old = samples[-lag] if len(samples) >= lag else None
                valid = old and all(x is not None for x in samples[-lag:])
                p['mom' + str(lag)] = (q['mid'] / old['q']['mid'] - 1) * 10000 if valid else None
            old5 = samples[-5] if len(samples) >= 5 else None
            p['oracle5'] = (c['oracle'] / old5['oracle'] - 1) * 10000 if old5 else None
            history = [x['q']['mid'] for x in samples[-60:] if x]
            sd = statistics.stdev(history) if len(history) == 60 else 0
            p['z60'] = (q['mid'] - statistics.mean(history)) / sd if sd > 0 else None
            samples.append(p)
        grids[s] = samples
        quality[s] = dict(points=len(samples), missing=missing,
                          valid=sum(p is not None for p in samples),
                          first_book=utc(qs[0]['t']), last_book=utc(qs[-1]['t']))
    for s, rows in grids.items():
        for i, p in enumerate(rows):
            if p:
                btc = grids['pas:BTC'][i]
                p['btc_lead'] = btc['mom5'] if btc and s in ('pas:ETH', 'pas:HYPE') else None
    return grids, quality, start, end


def threshold(x, limit, reverse=False):
    if x is None or abs(x) < limit or x == 0:
        return 0
    return (1 if x > 0 else -1) * (-1 if reverse else 1)


def signal(p, rule):
    if rule.startswith('mom'):
        return threshold(p[rule], {'mom5': 10, 'mom60': 20, 'mom240': 40}[rule])
    if rule in ('mr60', 'breakout60'):
        return threshold(p['z60'], 1.5, rule == 'mr60')
    if rule.startswith('basis'):
        return threshold(p['basis'], int(rule[5:]), True)
    if rule.startswith('funding'):
        return threshold(p[rule], 1e-12, True)
    return threshold(p[rule], .2 if rule == 'imbalance' else 10)


def quantity(notional, price, decimals):
    step = Decimal(10) ** (-decimals)
    return float((Decimal(str(notional)) / Decimal(str(price)) / step).to_integral_value(rounding=ROUND_FLOOR) * step)


def execution(qs, times, t, old_event, side, qty=None, entry=True, limit=None):
    # Entry needs a new event, not just a repeated receipt of an unchanged book.
    i = bisect_right(times, t) if entry else bisect_left(times, t)
    ceiling = t + 90000 if entry else limit
    while i < len(qs) and qs[i]['t'] < ceiling:
        q = qs[i]
        if q['event'] > old_event:
            touch = q['ask'] if side == 1 else q['bid']
            actual_qty = quantity(100, touch * (1 + side * 2 / 10000), q['decimals']) if qty is None else qty
            if actual_qty * touch < 10:
                return None, 'minimum'
            depth = q['az'] if side == 1 else q['bs']
            if actual_qty <= depth + 1e-12:
                return (q, actual_qty), None
            if entry:
                return None, 'entry_depth'
        i += 1
    return None, 'no_new_book' if entry else 'exit_unresolved'


def funding_integral(events, contexts, ct, entry, exit):
    rate_value, missing = 0., []
    included_hours = set()
    for f in events:
        if entry < f['event'] <= exit:
            c = past(contexts, ct, f['event'])
            if not c:
                missing.append('oracle:' + str(f['event']))
                continue
            included_hours.add(f['event'] // HOUR)
            rate_value += f['rate'] * c['oracle']
    for h in range(entry // HOUR + 1, exit // HOUR + 1):
        # Allow settlement millisecond offsets; don't invent a payment before it occurs.
        if h * HOUR + 1000 <= exit and h not in included_hours:
            missing.append('hour:' + str(h))
    return rate_value, missing


def payoff(a, b, qty, side, integral, fee=9, slip=2):
    et, xt = (a['ask'], b['bid']) if side == 1 else (a['bid'], b['ask'])
    ep, xp = et * (1 + side * slip / 10000), xt * (1 - side * slip / 10000)
    mid = side * qty * (b['mid'] - a['mid'])
    touch = side * qty * (xt - et)
    gross = side * qty * (xp - ep)
    fees, funding = qty * (ep + xp) * fee / 10000, -side * qty * integral
    return dict(mid_gross=mid, spread=mid - touch, slippage=touch - gross, fees=fees,
                funding=funding, net=gross - fees + funding, entry_price=ep, exit_price=xp)


def simulate(qs, ps, events, contexts, start, end, rule, hold, decision_delay_ms=0):
    times, ct = [q['t'] for q in qs], [c['t'] for c in contexts]
    trades, rejects, blocked_until = [], Counter(), start - 1
    end_entry = end - max(4 * HOUR, hold * MINUTE + 90000)
    for p in ps:
        if not p or p['t'] < start or p['t'] >= end_entry or p['t'] <= blocked_until:
            continue
        side = signal(p, rule)
        if not side:
            continue
        found, error = execution(qs, times, p['t'] + decision_delay_ms, p['q']['event'], side)
        if error:
            rejects[error] += 1
            continue
        a, qty = found
        if past(contexts, ct, a['t']) is None:
            rejects['entry_context_stale'] += 1
            continue
        found, error = execution(qs, times, a['t'] + hold * MINUTE, a['event'], -side,
                                 qty=qty, entry=False, limit=end)
        if error:
            rejects[error] += 1
            blocked_until = end
            break
        b, _ = found
        integral, missing = funding_integral(events, contexts, ct, a['t'], b['t'])
        base = payoff(a, b, qty, side, integral)
        stress = payoff(a, b, qty, side, integral, 18, 4)
        touch = payoff(a, b, qty, side, integral, 0, 0)
        longs, shorts = payoff(a, b, qty, 1, integral), payoff(a, b, qty, -1, integral)
        # Paired direction baselines must have evidence of both sides' visible depth.
        matched = min(a['bs'], a['az'], b['bs'], b['az']) + 1e-12 >= qty
        trades.append(dict(signal_ms=p['t'], entry_ms=a['t'], exit_ms=b['t'], entry_seq=a['seq'],
                           exit_seq=b['seq'], side=side, qty=qty, entry_bid=a['bid'], entry_ask=a['ask'],
                           exit_bid=b['bid'], exit_ask=b['ask'], entry_mid=a['mid'], exit_mid=b['mid'],
                           holding_minutes=(b['t'] - a['t']) / MINUTE, funding_integral=integral,
                           funding_complete=not missing, missing_funding=missing,
                           paired_depth=matched, matched_long_net=longs['net'] if matched else None,
                           matched_short_net=shorts['net'] if matched else None,
                           stress_net=stress['net'], touch_net=touch['net'], **base))
        blocked_until = b['t']
    return trades, dict(rejects)


def stats(trades, rejects):
    daily = defaultdict(float)
    for t in trades:
        daily[utc(t['exit_ms'])[:10]] += t['net']
    pairs = [t for t in trades if t['paired_depth']]
    fields = ('mid_gross', 'spread', 'slippage', 'fees', 'funding', 'net', 'stress_net', 'touch_net')
    out = {k: sum(t[k] for t in trades) for k in fields}
    out.update(n=len(trades), win_rate=sum(t['net'] > 0 for t in trades) / len(trades) if trades else None,
               mean_net_bps=statistics.mean(t['net'] / (t['qty'] * t['entry_mid']) * 10000 for t in trades) if trades else None,
               worst_trade=min((t['net'] for t in trades), default=None), daily=dict(sorted(daily.items())),
               funding_incomplete=sum(not t['funding_complete'] for t in trades), rejects=rejects,
               paired_n=len(pairs), paired_net=sum(t['net'] for t in pairs),
               matched_long_net=sum(t['matched_long_net'] for t in pairs),
               matched_short_net=sum(t['matched_short_net'] for t in pairs))
    out['resolved'] = not rejects.get('exit_unresolved', 0)
    out['paired_direction_excess'] = out['paired_net'] - max(out['matched_long_net'], out['matched_short_net'])
    return out


def pearson(a, b):
    if len(a) < 3:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    aa, bb = sum((v-ma)**2 for v in a), sum((v-mb)**2 for v in b)
    return sum((x-ma)*(y-mb) for x, y in zip(a, b)) / math.sqrt(aa*bb) if aa and bb else None


def diagnostics(qs, ps, segments):
    times = [q['t'] for q in qs]
    features = ('mom5', 'mom60', 'mom240', 'z60', 'basis', 'funding_last', 'funding_context', 'imbalance', 'oracle5', 'btc_lead')
    rows = []
    for name, start, end in segments:
        for hold in HOLDS:
            valid = []
            for p in ps:
                if not p or not start <= p['t'] < end - hold * MINUTE - 90000:
                    continue
                # Fixed nonoverlapping clock bins; descriptive return labels only.
                if (p['t'] // MINUTE) % hold:
                    continue
                found, _ = execution(qs, times, p['t'], p['q']['event'], 1)
                if not found:
                    continue
                a, _ = found
                i = bisect_left(times, a['t'] + hold * MINUTE)
                if i >= len(qs) or qs[i]['t'] - (a['t'] + hold * MINUTE) > 90000:
                    continue
                valid.append((p, (qs[i]['mid'] / a['mid'] - 1) * 10000))
            for f in features:
                pairs = [(p[f], y) for p, y in valid if p[f] is not None]
                rows.append(dict(split=name, hold_minutes=hold, feature=f, n=len(pairs),
                                 pearson=pearson([x for x, y in pairs], [y for x, y in pairs])))
    return rows


def daily_sign_p(daily):
    vals = list(daily.values())
    if not vals:
        return None
    observed = sum(vals)
    totals = [sum((1 if mask & (1 << i) else -1) * x for i, x in enumerate(vals))
              for mask in range(1 << len(vals))]
    return sum(v >= observed - 1e-12 for v in totals) / len(totals)


def run(input_path, output):
    books, contexts, funds, settlements, manifest = load(input_path)
    grids, quality, start, end = panel(books, contexts, funds)
    segments = [('development', start, CUT1), ('validation', CUT1, CUT2), ('audit', CUT2, end)]
    assert start < CUT1 < CUT2 < end
    results, ledger, factors, baselines = [], [], [], []
    for symbol in sorted(books):
        qs, ps, cs = books[symbol], grids[symbol], contexts[symbol]
        factors.extend(dict(symbol=symbol, **r) for r in diagnostics(qs, ps, segments))
        if symbol == 'pas:USDJPY':
            continue
        for name, a, b in segments:
            qt = [q['t'] for q in qs]
            first = bisect_right(qt, a)
            last = bisect_left(qt, b) - 1
            if first < last:
                x, y = qs[first], qs[last]
                integral, missing = funding_integral(settlements[symbol], cs, [c['t'] for c in cs], x['t'], y['t'])
                for side in (1, -1):
                    qty = quantity(100, (x['ask'] if side == 1 else x['bid']) * (1 + side * .0002), x['decimals'])
                    depth = min(x['az'] if side == 1 else x['bs'], y['bs'] if side == 1 else y['az'])
                    baselines.append(dict(symbol=symbol, split=name, side=side, qty=qty,
                                          depth_supported=qty <= depth, funding_complete=not missing,
                                          **payoff(x, y, qty, side, integral)))
        for rule in RULES:
            for hold in HOLDS:
                record = dict(symbol=symbol, rule=rule, hold_minutes=hold, splits={})
                all_trades = []
                for name, a, b in segments:
                    trades, rejected = simulate(qs, ps, settlements[symbol], cs, a, b, rule, hold)
                    record['splits'][name] = stats(trades, rejected)
                    ledger.extend(dict(symbol=symbol, rule=rule, hold_minutes=hold, split=name, **t) for t in trades)
                    all_trades.extend(trades)
                total = stats(all_trades, {})
                record['total'] = total
                pvalue = daily_sign_p(total['daily'])
                record['exploratory_daily_sign_p'] = pvalue
                record['bonferroni_p_303'] = min(1, pvalue * 303) if pvalue is not None else None
                d, v, a = [record['splits'][k] for k in ('development', 'validation', 'audit')]
                pair_net = v['paired_net'] + a['paired_net']
                best_direction = max(v['matched_long_net'] + a['matched_long_net'],
                                     v['matched_short_net'] + a['matched_short_net'])
                record['late_paired_direction_excess'] = pair_net - best_direction
                record['priority_candidate'] = (d['n'] >= 8 and v['n'] >= 3 and a['n'] >= 5
                    and v['net'] > 0 and a['net'] > 0 and v['stress_net'] + a['stress_net'] > 0
                    and pair_net > best_direction and v['funding_incomplete'] + a['funding_incomplete'] == 0
                    and not v['rejects'].get('exit_unresolved', 0) and not a['rejects'].get('exit_unresolved', 0))
                results.append(record)
        print(symbol, 'completed', flush=True)
    output.mkdir(parents=True, exist_ok=True)
    paths = [Path(__file__), Path('docs/research/weekly-synthesis-2026-10-26/alpha-screen-protocol.zh-CN.md')]
    report = dict(schema='pas-weekly-alpha-screen/1', generated_at=datetime.now(timezone.utc).isoformat(),
                  manifest=manifest, panel_quality=quality, start_utc=utc(start), end_utc=utc(end),
                  segments=[dict(name=n, start_utc=utc(a), end_utc=utc(b)) for n, a, b in segments],
                  source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                  valuation_excluded=['pas:USDJPY'], results=results, factors=factors, baselines=baselines)
    (output/'results.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    fields = ['symbol', 'rule', 'hold_minutes', 'split', 'n', 'mid_gross', 'spread', 'slippage', 'fees',
              'funding', 'net', 'stress_net', 'touch_net', 'mean_net_bps', 'win_rate', 'funding_incomplete',
              'paired_n', 'paired_direction_excess']
    with (output/'scenarios.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fields, extrasaction='ignore')
        w.writeheader()
        for r in results:
            for split, s in list(r['splits'].items()) + [('total', r['total'])]:
                w.writerow(dict(symbol=r['symbol'], rule=r['rule'], hold_minutes=r['hold_minutes'], split=split, **s))
    with gzip.GzipFile(filename=str(output/'trades.jsonl.gz'), mode='wb', mtime=0) as f:
        for t in ledger:
            f.write((canonical(t)+'\n').encode())
    print(canonical(dict(combinations=len(results), trades=len(ledger),
                         priority=[{k:r[k] for k in ('symbol','rule','hold_minutes')} for r in results if r['priority_candidate']])), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', type=Path)
    ap.add_argument('--output', required=True, type=Path)
    args = ap.parse_args()
    run(args.input, args.output)
