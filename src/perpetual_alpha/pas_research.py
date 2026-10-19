"""Offline PAS research: read-only raw extraction, causal minute panels and daily statistics."""
import argparse
import csv
import hashlib
import json
import math
import sqlite3
import statistics
from bisect import bisect_right
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .venue_collection import APPLICATION_ID as RAW_ID
from .venue_collection import URL, book_quality, canonical
from .venues import venue

APP_ID = 0x50525331
SOURCE = venue('pas').source
SYMBOLS = venue('pas').symbols
MINUTE = 60_000
DAY = 86_400_000
BOOK_TTL = 90_000
CONTEXT_TTL = 90_000
FUNDING_TTL = 7_200_000
SCHEMA = '''
CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), raw_path TEXT NOT NULL,
 identity TEXT NOT NULL, pipeline_hash TEXT NOT NULL, last_seq INTEGER NOT NULL, prefix_hash TEXT NOT NULL);
CREATE TABLE samples (
 seq INTEGER PRIMARY KEY, source TEXT NOT NULL, symbol TEXT, kind TEXT NOT NULL,
 event_ms INTEGER, requested_ms INTEGER NOT NULL, received_ms INTEGER NOT NULL,
 stored_ms INTEGER NOT NULL, available_ms INTEGER NOT NULL, raw_quality TEXT NOT NULL,
 quality TEXT NOT NULL, raw_sha256 TEXT NOT NULL, row_sha256 TEXT NOT NULL,
 bid REAL, ask REAL, mid REAL, spread_bps REAL, mark REAL, oracle REAL, context_funding REAL);
CREATE INDEX sample_asof ON samples(symbol,kind,available_ms,seq);
CREATE TABLE funding (
 seq INTEGER NOT NULL REFERENCES samples(seq), item INTEGER NOT NULL,
 symbol TEXT NOT NULL, event_ms INTEGER NOT NULL, available_ms INTEGER NOT NULL, rate REAL NOT NULL,
 PRIMARY KEY(seq,item));
CREATE INDEX funding_asof ON funding(symbol,available_ms,event_ms);
'''


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def raw_identity(db):
    if db.execute('PRAGMA application_id').fetchone()[0] != RAW_ID:
        raise ValueError('Not a public collector database')
    if db.execute('PRAGMA user_version').fetchone()[0] != 1:
        raise ValueError('Unsupported raw schema')
    rows = db.execute('SELECT value FROM identity').fetchall()
    expected = {'schema': 'public-venue-collection/1', 'source': SOURCE, 'dex': 'pas',
                'collateral': 'USDP', 'symbols': list(SYMBOLS), 'endpoint': URL}
    if len(rows) != 1 or json.loads(rows[0][0]) != expected:
        raise ValueError('PAS identity mismatch; never relabel PAR')
    return canonical(expected)


def finite(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and (not positive or number > 0) else None
    except (TypeError, ValueError, OverflowError):
        return None


def aliases(path, protected):
    path = Path(path).resolve()
    return any(path == Path(p).resolve() or (path.exists() and Path(p).exists() and path.samefile(p))
               for p in protected)


def database_files(path):
    return [Path(str(path)+suffix) for suffix in ('', '-wal', '-shm', '-journal')]


def normalize(row):
    """One record per receipt; invalid data remains an auditable null-valued observation."""
    r = dict(row)
    if r['symbol'] is not None and r['symbol'] not in SYMBOLS:
        raise ValueError('Non-PAS symbol in raw observations')
    times = [r[k] for k in ('requested_ms', 'received_ms', 'stored_ms')]
    if any(type(t) is not int or t < 0 for t in times) or times[0] > times[1]:
        raise ValueError('Invalid observation clock')
    request, raw = json.loads(r['request_json']), json.loads(r['raw_json'])
    if not isinstance(request, dict):
        raise ValueError('Malformed raw request')
    if request.get('dex', 'pas') != 'pas' or request.get('coin', r['symbol']) != r['symbol']:
        raise ValueError('Cross-venue request')
    if hashlib.sha256(r['raw_json'].encode()).hexdigest() != r['sha256']:
        raise ValueError('Raw payload checksum mismatch')
    result = {k: r[k] for k in ('seq', 'symbol', 'kind', 'event_ms', 'requested_ms',
                                'received_ms', 'stored_ms')}
    result.update(source=SOURCE, available_ms=max(times), raw_quality=r['quality'],
                  quality=r['quality'], raw_sha256=r['sha256'],
                  row_sha256=hashlib.sha256(canonical(r).encode()).hexdigest())
    result.update(dict.fromkeys(('bid', 'ask', 'mid', 'spread_bps', 'mark', 'oracle', 'context_funding')))
    funding = []
    if r['kind'] == 'book':
        quality = book_quality(r['symbol'], raw, r['received_ms'])
        result['quality'] = quality if r['quality'] == 'ready' else r['quality']
        if result['quality'] == 'ready':
            bid, ask = (finite(side[0]['px'], True) for side in raw['levels'])
            sizes = [finite(side[0].get('sz'), True) for side in raw['levels']]
            if bid is None or ask is None or None in sizes or r['event_ms'] != raw['time']:
                result['quality'] = 'invalid-price-size-or-time'
            else:
                mid = bid / 2 + ask / 2
                result.update(bid=bid, ask=ask, mid=mid, spread_bps=(ask-bid)/mid*10_000)
    elif r['kind'] == 'context' and r['quality'] == 'observed':
        context = raw.get('context', {}) if isinstance(raw, dict) else {}
        context = context if isinstance(context, dict) else {}
        result.update(mark=finite(context.get('markPx'), True), oracle=finite(context.get('oraclePx'), True),
                      context_funding=finite(context.get('funding')))
        if result['mark'] is None or result['oracle'] is None:
            result['quality'] = 'invalid-context'
    elif r['kind'] == 'settled-funding' and r['quality'] == 'observed':
        if not isinstance(raw, list):
            result['quality'] = 'invalid-funding'
        else:
            for i, item in enumerate(raw):
                if (not isinstance(item, dict) or item.get('coin') != r['symbol']
                        or type(item.get('time')) is not int or item['time'] < 0
                        or item['time'] > r['received_ms'] or finite(item.get('fundingRate')) is None):
                    result['quality'] = 'invalid-funding'
                    funding = []
                    break
                funding.append((r['seq'], i, r['symbol'], item['time'], result['available_ms'],
                                finite(item['fundingRate'])))
    elif r['kind'] == 'request-error':
        result['kind'] = {'l2Book': 'book', 'metaAndAssetCtxs': 'metadata-context',
                          'fundingHistory': 'settled-funding'}.get(request.get('type'), 'request-error')
    return result, funding


def ingest(raw_path, output, batch_size=1000):
    """Snapshot raw in WAL-aware read-only mode; commit rows and cursor atomically per batch.

    The complete committed prefix is hashed again on resume to reject replacement,
    truncation or edits. This is streaming O(n) verification, not O(n) normalization.
    """
    raw_path, output = Path(raw_path).resolve(), Path(output).resolve()
    pipeline_hash = hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest()
    if aliases(output, database_files(raw_path)):
        raise ValueError('Derived output must differ from raw input')
    if batch_size <= 0:
        raise ValueError('Positive batch size required')
    with closing(readonly(raw_path)) as raw:
        raw.execute('BEGIN')
        identity = raw_identity(raw)
        output.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(output, timeout=15)) as out:
            out.row_factory = sqlite3.Row
            out.execute('PRAGMA foreign_keys=ON')
            tables = out.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if not tables:
                out.executescript('BEGIN IMMEDIATE;\n'+SCHEMA)
                out.execute(f'PRAGMA application_id={APP_ID}')
                out.execute('PRAGMA user_version=1')
                out.execute('INSERT INTO state VALUES(1,?,?,?,0,?)',
                            (str(raw_path), identity, pipeline_hash, hashlib.sha256().hexdigest()))
                out.commit()
            if (out.execute('PRAGMA application_id').fetchone()[0] != APP_ID
                    or out.execute('PRAGMA user_version').fetchone()[0] != 1):
                raise ValueError('Foreign derived database; use a new output path')
            # Serialize writers for this output; never acquire a raw write lock.
            out.execute('BEGIN IMMEDIATE')
            state = out.execute('SELECT * FROM state WHERE id=1').fetchone()
            if state['raw_path'] != str(raw_path) or state['identity'] != identity:
                raise ValueError('Derived input binding mismatch; rebuild into a new file')
            if state['pipeline_hash'] != pipeline_hash:
                raise ValueError('Pipeline code changed; rebuild into a new file')
            digest = hashlib.sha256()
            last = 0
            for row in raw.execute('SELECT * FROM observations WHERE seq<=? ORDER BY seq', (state['last_seq'],)):
                digest.update(canonical(dict(row)).encode() + b'\n')
                last = row['seq']
            if last != state['last_seq'] or digest.hexdigest() != state['prefix_hash']:
                raise ValueError('Raw committed prefix changed; rebuild into a new file')
            count = 0
            for row in raw.execute('SELECT * FROM observations WHERE seq>? ORDER BY seq', (last,)):
                sample, funds = normalize(row)
                columns = ','.join(sample)
                out.execute(f'INSERT INTO samples({columns}) VALUES({",".join("?" for _ in sample)})',
                            tuple(sample.values()))
                out.executemany('INSERT INTO funding VALUES(?,?,?,?,?,?)', funds)
                digest.update(canonical(dict(row)).encode() + b'\n')
                last = row['seq']
                out.execute('UPDATE state SET last_seq=?,prefix_hash=? WHERE id=1', (last, digest.hexdigest()))
                count += 1
                if count % batch_size == 0:
                    out.commit()
                    out.execute('BEGIN IMMEDIATE')
            out.commit()
            return {'inserted': count, 'last_seq': last, 'prefix_hash': digest.hexdigest(), 'source': SOURCE}


def panel(db, start, end):
    """Half-open UTC minute decision grid; each field keeps independent receipt lineage."""
    if start % MINUTE or end % MINUTE or end <= start:
        raise ValueError('Use a nonempty minute-aligned [start,end) interval')
    series = {}
    for symbol in SYMBOLS:
        kinds = {}
        for kind in ('book', 'context'):
            rows = list(db.execute('SELECT * FROM samples WHERE symbol=? AND kind=? AND available_ms<? '
                                   'ORDER BY available_ms,seq', (symbol, kind, end)))
            kinds[kind] = ([r['available_ms'] for r in rows], rows)
        funds = list(db.execute('SELECT f.*,s.requested_ms,s.received_ms,s.stored_ms,s.raw_quality,s.quality '
                                'FROM funding f JOIN samples s ON s.seq=f.seq '
                                'WHERE f.symbol=? AND f.available_ms<? '
                                'ORDER BY f.available_ms,f.seq,f.item', (symbol, end)))
        series[symbol] = (kinds, funds)
    for symbol, (kinds, funds) in series.items():
        previous = None
        fund_index, known = 0, {}
        for decision in range(start, end, MINUTE):
            record = {'source': SOURCE, 'symbol': symbol, 'decision_ms': decision}
            chosen = {}
            for kind, (times, rows) in kinds.items():
                pos = bisect_right(times, decision)-1
                row = rows[pos] if pos >= 0 else None
                chosen[kind] = row
                for field in ('seq', 'event_ms', 'requested_ms', 'received_ms', 'stored_ms', 'available_ms',
                              'raw_quality', 'quality'):
                    record[f'{kind}_{field}'] = row[field] if row else None
            book, context = chosen['book'], chosen['context']
            quote_ok = bool(book and book['quality'] == 'ready' and book['event_ms'] is not None
                            and 0 <= decision-book['event_ms'] <= BOOK_TTL
                            and decision-book['received_ms'] <= BOOK_TTL)
            context_ok = bool(context and context['quality'] == 'observed'
                              and 0 <= decision-context['received_ms'] <= CONTEXT_TTL)
            record['quote_valid'] = quote_ok
            record['context_valid'] = context_ok
            for field in ('bid', 'ask', 'mid', 'spread_bps'):
                record[field] = book[field] if quote_ok else None
            for field in ('mark', 'oracle', 'context_funding'):
                record[field] = context[field] if context_ok else None
            record['basis_bps'] = (finite((record['mid']/record['oracle']-1)*10_000)
                                   if quote_ok and context_ok else None)
            record['book_request_latency_ms'] = book['received_ms']-book['requested_ms'] if book else None
            record['book_event_age_ms'] = decision-book['event_ms'] if book and book['event_ms'] else None
            record['context_age_ms'] = decision-context['received_ms'] if context else None
            record['log_return'] = None
            if quote_ok and previous and previous['quote_valid']:
                if (book['event_ms'] > previous['book_event_ms']
                        and book['seq'] != previous['book_seq']):
                    record['log_return'] = math.log(record['mid'])-math.log(previous['mid'])
            while fund_index < len(funds) and funds[fund_index]['available_ms'] <= decision:
                f = funds[fund_index]
                known[f['event_ms']] = f
                fund_index += 1
            events = sorted(t for t in known if t <= decision)
            latest = known[events[-1]] if events else None
            valid_funding = latest if latest and decision-latest['event_ms'] <= FUNDING_TTL else None
            for field in ('seq', 'event_ms', 'requested_ms', 'received_ms', 'stored_ms',
                          'available_ms', 'raw_quality', 'quality', 'rate'):
                record['funding_'+field] = valid_funding[field] if valid_funding else None
            record['funding_change'] = None
            if valid_funding and len(events) > 1 and events[-1]-events[-2] == 3_600_000:
                record['funding_change'] = finite(valid_funding['rate']-known[events[-2]]['rate'])
            previous = record
            yield record


def checked_derived(path):
    db = readonly(path)
    if (db.execute('PRAGMA application_id').fetchone()[0] != APP_ID
            or db.execute('PRAGMA user_version').fetchone()[0] != 1):
        db.close()
        raise ValueError('Not a PAS research database')
    return db


def summarize(db, start, end):
    rows = list(panel(db, start-MINUTE, end))  # one preceding decision for first return
    results = []
    for symbol in SYMBOLS:
        points = [r for r in rows if r['symbol'] == symbol and r['decision_ms'] >= start]
        def values(field):
            return [r[field] for r in points if r[field] is not None]
        returns = values('log_return')
        spread, basis = values('spread_bps'), values('basis_bps')
        receipts = list(db.execute("SELECT * FROM samples WHERE symbol=? AND kind='book' "
                                   'AND available_ms>=? AND available_ms<? ORDER BY available_ms,seq',
                                   (symbol, start, end)))
        funding = db.execute('SELECT COUNT(DISTINCT event_ms) FROM funding '
                             'WHERE symbol=? AND available_ms<? AND event_ms>=? AND event_ms<?',
                             (symbol, end, start, end)).fetchone()[0]
        summary = {'source': SOURCE, 'symbol': symbol, 'grid_points': len(points),
                   'valid_quotes': sum(r['quote_valid'] for r in points),
                   'valid_quote_ratio': sum(r['quote_valid'] for r in points)/len(points),
                   'missing_quote_points': sum(r['book_seq'] is None for r in points),
                   'unusable_quote_points': sum(not r['quote_valid'] and r['book_seq'] is not None for r in points),
                   'missing_context_points': sum(not r['context_valid'] for r in points),
                   'book_receipts': len(receipts),
                   'invalid_book_receipts': sum(r['quality'] != 'ready' for r in receipts),
                   'return_samples': len(returns), 'spread_samples': len(spread), 'basis_samples': len(basis),
                   'mean_spread_bps': statistics.mean(spread) if spread else None,
                   'mean_basis_bps': statistics.mean(basis) if basis else None,
                   'mean_log_return': statistics.mean(returns) if returns else None,
                   'minute_volatility': statistics.stdev(returns) if len(returns) >= 2 else None,
                   'realized_variation': math.sqrt(sum(x*x for x in returns)) if returns else None,
                   'unique_settled_funding_events': funding}
        latencies = [r['received_ms']-r['requested_ms'] for r in receipts]
        summary['mean_request_latency_ms'] = statistics.mean(latencies) if latencies else None
        summary['max_request_latency_ms'] = max(latencies) if latencies else None
        summary['delayed_book_receipts'] = sum(t > 5000 for t in latencies)
        longest, streak = 0, 0
        for point in points:
            streak = 0 if point['quote_valid'] else streak+1
            longest = max(longest, streak)
        summary['longest_unusable_minutes'] = longest
        summary['quality_counts'] = canonical({q: sum(r['quality'] == q for r in receipts)
                                               for q in sorted({r['quality'] for r in receipts})})
        results.append(summary)
    return results


def write_csv(path, rows):
    rows = iter(rows)
    first = next(rows, None)
    if first is None:
        raise ValueError('No export records')
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(first))
        writer.writeheader()
        writer.writerow(first)
        writer.writerows(rows)


def report(database, date, directory, label='UNVERIFIED INPUT'):
    start = int(datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()*1000)
    directory = Path(directory).resolve()
    # Never let an export overwrite its own database, even under a misleading suffix.
    targets = [directory/f'{date}-{suffix}' for suffix in
               ('panel.csv', 'statistics.csv', 'report.md', 'observations.csv', 'funding.csv')]
    if any(aliases(p, database_files(database)) for p in targets):
        raise ValueError('Report output aliases the research database')
    directory.mkdir(parents=True, exist_ok=True)
    with closing(checked_derived(database)) as db:
        db.execute('BEGIN')
        state = dict(db.execute('SELECT * FROM state').fetchone())
        if any(aliases(p, database_files(state['raw_path'])) for p in targets):
            raise ValueError('Report output aliases the raw database')
        stats = summarize(db, start, start+DAY)
        write_csv(targets[0], (r for r in panel(db, start-MINUTE, start+DAY) if r['decision_ms'] >= start))
        write_csv(targets[1], stats)
        # Schema headers also exist on days with zero receipts.
        for path, query in ((targets[3], 'SELECT * FROM samples WHERE available_ms>=? AND available_ms<? '
                            'ORDER BY available_ms,seq'),
                           (targets[4], 'SELECT s.source,f.*,s.requested_ms,s.received_ms,s.stored_ms,'
                            's.raw_quality,s.quality FROM funding f JOIN samples s ON s.seq=f.seq '
                            'WHERE f.available_ms>=? AND f.available_ms<? '
                            'ORDER BY f.available_ms,f.seq,f.item')):
            cursor = db.execute(query, (start, start+DAY))
            with path.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow([c[0] for c in cursor.description])
                writer.writerows(cursor)
        state.pop('raw_path')
        lines = [f'# PAS research / {date} UTC', '',
                 f'Dataset label: {label}', '',
                 'Descriptive observations only; no executable returns or independent alpha evidence.', '',
                 'Protocol: pas-research/1; minute decision grid, 90s book/context TTL, 2h settled Funding TTL.',
                 'Availability=max(requested,received,stored); stored is a transaction sample, not commit time.',
                 'Each component retains independent times; the panel is not an atomic market snapshot.',
                 'Returns: adjacent valid minute points with advancing book event time; gaps/repeats excluded.',
                 'Volatility: sample stdev of minute log returns (not annualized); realized variation=sqrt(sum(r²)).',
                 'Spread=(ask-bid)/mid*10000; basis=(mid/oracle-1)*10000; no future fill or interpolation.',
                 'Quality ratios use 1440 scheduled UTC decisions, including unobserved periods. Means are grid-weighted.',
                 'Funding counts deduplicate settlement times as known before day end; no missing rates imputed.', '',
                 '|Symbol|Valid / grid|Book receipts / invalid|Return n|Spread bps|Basis bps|Minute vol|Funding events|',
                 '|---|---:|---:|---:|---:|---:|---:|---:|']
        def fmt(v):
            return 'unknown' if v is None else f'{v:.6g}'
        for s in stats:
            lines.append(f"|{s['symbol']}|{s['valid_quotes']}/{s['grid_points']}|"
                         f"{s['book_receipts']}/{s['invalid_book_receipts']}|{s['return_samples']}|"
                         f"{fmt(s['mean_spread_bps'])}|{fmt(s['mean_basis_bps'])}|"
                         f"{fmt(s['minute_volatility'])}|{s['unique_settled_funding_events']}|")
        lines += ['', 'Limitations: no trades, queue position, fills, fees or execution simulation; minute REST',
                  'observations cannot reconstruct intraminute paths. Oracle event time is unknown. Context Funding',
                  'is a separate indicative field, not settled cashflow. Short/correlated samples do not establish',
                  'statistical significance. Gaps make variation partial; absent values remain unknown.', '',
                  'Input manifest (checkpoint identifies the raw prefix; outputs contain no wall-clock generation time):',
                  '```json', json.dumps(state, sort_keys=True, indent=2), '```', '']
        targets[2].write_text('\n'.join(lines), encoding='utf-8')
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    update = sub.add_parser('update')
    update.add_argument('--raw', type=Path, required=True)
    update.add_argument('--derived', type=Path, required=True)
    update.add_argument('--batch-size', type=int, default=1000)
    daily = sub.add_parser('report')
    daily.add_argument('--derived', type=Path, required=True)
    daily.add_argument('--date', required=True)
    daily.add_argument('--output', type=Path, required=True)
    daily.add_argument('--label', default='UNVERIFIED INPUT', help='Provenance label, not a validation claim')
    args = parser.parse_args()
    if args.command == 'update':
        print(json.dumps(ingest(args.raw, args.derived, args.batch_size), sort_keys=True))
    else:
        report(args.derived, args.date, args.output, args.label)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
