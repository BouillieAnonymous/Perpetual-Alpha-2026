"""Bounded, read-only PAS CSV export; SSH callers have no path or SQL options."""
import argparse
import csv
import io
import os
import re
import shlex
import signal
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from . import pas_research as research

DATABASE = Path('/opt/perpetual-alpha-pas-data/pas/public.sqlite3')
LOCK = Path('/run/alpha-export/export.lock')
BATCH = 256
SECONDS = 300
QUERY_SECONDS = 5
SAMPLE_FIELDS = ('seq', 'symbol', 'kind', 'event_ms', 'requested_ms', 'received_ms', 'stored_ms',
                 'source', 'available_ms', 'raw_quality', 'quality', 'raw_sha256', 'row_sha256',
                 'bid', 'ask', 'mid', 'spread_bps', 'mark', 'oracle', 'context_funding')
FUND_FIELDS = ('source', 'seq', 'item', 'symbol', 'event_ms', 'available_ms', 'rate')


def utc(value):
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z', value):
        raise ValueError('Use UTC ISO timestamps ending in Z')
    return int(datetime.fromisoformat(value[:-1] + '+00:00').timestamp() * 1000)


def arguments(tokens):
    parser = argparse.ArgumentParser(prog='export', allow_abbrev=False, add_help=False)
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--from', dest='start', type=utc)
    parser.add_argument('--to', dest='end', type=utc)
    parser.add_argument('--symbol', choices=[s.removeprefix('pas:') for s in research.SYMBOLS])
    parser.add_argument('--dataset', choices=['observations', 'funding', 'minute-panel'], default='observations')
    parser.add_argument('--raw-json', action='store_true')
    # Reject duplicate flags, abbreviations, equals forms and all shell syntax.
    flags = [t for t in tokens if t.startswith('--')]
    if len(flags) != len(set(flags)) or any('=' in t for t in flags):
        raise ValueError('Duplicate or noncanonical options')
    args = parser.parse_args(tokens)
    if args.all == (args.start is not None or args.end is not None):
        raise ValueError('Choose --all or both --from and --to')
    if not args.all and (args.start is None or args.end is None or not 0 <= args.start < args.end):
        raise ValueError('Use a nonempty UTC [from,to) range')
    if args.raw_json and args.dataset != 'observations':
        raise ValueError('--raw-json requires observations')
    if args.dataset == 'minute-panel' and not args.all and (args.start % 60000 or args.end % 60000):
        raise ValueError('Minute panel bounds must be minute aligned')
    return args


def ssh_arguments(command):
    if len(command) > 1024 or not re.fullmatch(r'[A-Za-z0-9 _:.,\-TZ]*', command):
        raise ValueError('Invalid export command')
    tokens = shlex.split(command)
    if not tokens or tokens.pop(0) != 'export':
        raise ValueError('Only export is allowed')
    return arguments(tokens)


class Reader:
    """Append-only prefix, short transactions; never hold a cursor while writing stdout."""
    def __init__(self, path):
        self.db = research.readonly(path)
        try:
            self.db.execute('PRAGMA busy_timeout=1000')
            self.db.execute('PRAGMA temp_store=MEMORY')
            self.db.execute('PRAGMA cache_size=-4096')
            self.deadline = time.monotonic() + SECONDS
            self.query_deadline = self.deadline
            self.db.set_progress_handler(self.progress, 1000)
            self.query('SELECT 1')
            research.raw_identity(self.db)
            self.maximum = self.query('SELECT COALESCE(MAX(seq),0) FROM observations')[0][0]
        except BaseException:
            self.db.close()
            raise

    def progress(self):
        return int(time.monotonic() > min(self.deadline, self.query_deadline))

    def query(self, sql, params=()):
        self.query_deadline = min(self.deadline, time.monotonic() + QUERY_SECONDS)
        with closing(self.db.execute(sql, params)) as cursor:
            return cursor.fetchall()

    def rows(self, ordered=False):
        last, stamp = 0, -1
        while True:
            if ordered:
                rows = self.query('SELECT *,max(requested_ms,received_ms,stored_ms) AS export_ms '
                                  'FROM observations WHERE seq<=? AND '
                                  '(max(requested_ms,received_ms,stored_ms),seq)>(?,?) '
                                  'ORDER BY export_ms,seq LIMIT ?', (self.maximum, stamp, last, BATCH))
            else:
                rows = self.query('SELECT * FROM observations WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?',
                                  (last, self.maximum, BATCH))
            if not rows:
                break
            for row in rows:
                record = dict(row)
                record.pop('export_ms', None)
                yield record
            last = rows[-1]['seq']
            if ordered:
                stamp = rows[-1]['export_ms']

    def close(self):
        self.db.close()


def observations(reader, args):
    for raw in reader.rows():
        sample, funds = research.normalize(raw)
        if args.symbol and sample['symbol'] != 'pas:' + args.symbol:
            continue
        if not args.all and not args.start <= sample['available_ms'] < args.end:
            continue
        if args.dataset == 'funding':
            for fund in funds:
                yield dict(zip(FUND_FIELDS, (research.SOURCE, *fund)))
        else:
            if args.raw_json:
                sample.update(request_json=raw['request_json'], raw_json=raw['raw_json'])
            yield sample


def compact(window, decision):
    # Keep all rows in previous minute plus latest older component (including invalidity lineage).
    window.execute('DELETE FROM samples WHERE kind NOT IN (\'book\',\'context\') '
                   'AND seq NOT IN (SELECT seq FROM funding)')
    window.execute('DELETE FROM samples WHERE kind IN (\'book\',\'context\') AND available_ms<? '
                   'AND seq NOT IN (SELECT seq FROM samples s WHERE available_ms<? AND seq=('
                   'SELECT seq FROM samples t WHERE t.symbol=s.symbol AND t.kind=s.kind '
                   'AND t.available_ms<? ORDER BY available_ms DESC,seq DESC LIMIT 1))',
                   (decision-60000, decision-60000, decision-60000))
    window.execute('DELETE FROM funding WHERE event_ms<?',
                   (decision-research.FUNDING_TTL-3_600_000-60000,))
    # Repeated receipts for the same event need only the newest visible version.
    window.execute('DELETE FROM funding WHERE (seq,item) NOT IN '
                   '(SELECT seq,item FROM funding f WHERE (seq,item)=('
                   'SELECT seq,item FROM funding t WHERE t.symbol=f.symbol AND t.event_ms=f.event_ms '
                   'ORDER BY available_ms DESC,seq DESC,item DESC LIMIT 1)) AND available_ms<?',
                   (decision-60000,))
    window.commit()


def minute_panel(reader, args):
    # Reuse the existing causal panel calculation on a bounded rolling memory window.
    bounds = reader.query('SELECT min(max(requested_ms,received_ms,stored_ms)), '
                          'max(max(requested_ms,received_ms,stored_ms)) FROM observations WHERE seq<=?',
                          (reader.maximum,))[0]
    if bounds[0] is None:
        return
    start = (bounds[0] // 60000 * 60000) if args.all else args.start
    end = (bounds[1] // 60000 + 1) * 60000 if args.all else args.end
    with closing(sqlite3.connect(':memory:')) as window:
        window.row_factory = sqlite3.Row
        window.executescript(research.SCHEMA)
        raw_rows = iter(reader.rows(ordered=True))
        pending = next(raw_rows, None)
        for decision in range(max(0, start-60000), end, 60000):
            while pending is not None and max(pending[k] for k in
                                               ('requested_ms', 'received_ms', 'stored_ms')) <= decision:
                sample, funds = research.normalize(pending)
                window.execute(f'INSERT INTO samples({",".join(sample)}) VALUES({",".join("?" for _ in sample)})',
                               tuple(sample.values()))
                window.executemany('INSERT INTO funding VALUES(?,?,?,?,?,?)', funds)
                if sample['seq'] % BATCH == 0:
                    compact(window, decision)
                pending = next(raw_rows, None)
            if decision >= start:
                for record in research.panel(window, max(0, decision-60000), decision+60000):
                    if record['decision_ms'] == decision and (
                            not args.symbol or record['symbol'] == 'pas:' + args.symbol):
                        yield record
            compact(window, decision)


def export(path, args, output):
    with closing(Reader(path)) as reader:
        if args.dataset == 'minute-panel':
            rows = minute_panel(reader, args)
            # Stable headers even for empty databases.
            with closing(sqlite3.connect(':memory:')) as empty:
                empty.row_factory = sqlite3.Row
                empty.executescript(research.SCHEMA)
                fields = list(next(research.panel(empty, 0, 60000)))
        else:
            rows = observations(reader, args)
            fields = list(FUND_FIELDS if args.dataset == 'funding' else SAMPLE_FIELDS)
            if args.raw_json:
                fields += ['request_json', 'raw_json']
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            if time.monotonic() > reader.deadline:
                raise TimeoutError('Export time limit exceeded')
            writer.writerow(row)
        output.flush()


def limited_session():
    import fcntl
    import resource
    lock = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        raise ValueError('Another export is active; retry later') from None
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    def expired(*_):
        # Cleanup/flush must not hang forever if the receiver stopped reading.
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(1)
        raise TimeoutError('Export time limit exceeded')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(SECONDS)
    return lock


def main():
    lock = None
    try:
        args = ssh_arguments(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
        lock = limited_session()
        with io.TextIOWrapper(os.fdopen(os.dup(1), 'wb'), encoding='utf-8', newline='') as output:
            export(DATABASE, args, output)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'Export failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            os.close(lock)


if __name__ == '__main__':
    sys.exit(main())
