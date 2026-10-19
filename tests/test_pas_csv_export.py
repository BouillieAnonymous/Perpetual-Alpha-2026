import csv
import importlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from perpetual_alpha.examples.pas_research_fixture import START, append, book, create
from perpetual_alpha.venue_collection import Capture

PACKAGE = os.environ.get('CSV_EXPORT_TEST_PACKAGE', 'perpetual_alpha')
export = importlib.import_module(PACKAGE + '.pas_csv_export')
research = importlib.import_module(PACKAGE + '.pas_research')

class CSVExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / 'public.sqlite3'
        create(self.raw)

    def rows(self, command):
        output = io.StringIO()
        export.export(self.raw, export.ssh_arguments(command), output)
        return list(csv.DictReader(io.StringIO(output.getvalue())))

    def test_all_matches_normalization_and_raw_payload(self):
        with closing(research.readonly(self.raw)) as db:
            expected = [research.normalize(r)[0] for r in db.execute('SELECT * FROM observations ORDER BY seq')]
            before = db.execute('SELECT count(*) FROM observations').fetchone()[0]
        rows = self.rows('export --all --raw-json')
        self.assertEqual(len(rows), before)
        self.assertEqual(rows[0]['raw_sha256'], expected[0]['raw_sha256'])
        self.assertEqual(rows[1]['mid'], str(expected[1]['mid']))
        self.assertIn('raw_json', rows[0])
        with closing(export.Reader(self.raw)) as reader:
            with self.assertRaises(sqlite3.OperationalError):
                reader.db.execute('DELETE FROM observations')

    def test_half_open_available_time_symbol_and_funding_versions(self):
        rows = self.rows('export --from 2026-10-19T00:01:00Z --to 2026-10-19T00:02:00Z --symbol BTC')
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(START+60000 <= int(r['available_ms']) < START+120000 for r in rows))
        self.assertTrue(all(r['symbol'] == 'pas:BTC' for r in rows))
        funding = self.rows('export --all --symbol BTC --dataset funding')
        self.assertEqual(len(funding), 4)  # overlapping observations preserved
        self.assertEqual({r['event_ms'] for r in funding}, {str(START-3600000), str(START)})

    def test_panel_matches_existing_pipeline_including_warmup(self):
        derived = Path(self.tmp.name) / 'derived.sqlite3'
        research.ingest(self.raw, derived)
        with closing(research.readonly(derived)) as db:
            expected = list(research.panel(db, START-60000, START+12*60000))
        expected = [r for r in expected if r['decision_ms'] >= START]
        rows = self.rows('export --from 2026-10-19T00:00:00Z --to 2026-10-19T00:12:00Z --dataset minute-panel')
        actual = {(r['symbol'], int(r['decision_ms'])): r for r in rows}
        for record in expected:
            self.assertEqual(actual[record['symbol'], record['decision_ms']],
                             {k: '' if v is None else str(v) for k, v in record.items()})

    def test_batches_release_wal_reader_and_exclude_concurrent_append(self):
        store = Capture(self.raw, 'pas')
        self.addCleanup(store.close)
        with closing(export.Reader(self.raw)) as reader, patch.object(export, 'BATCH', 2):
            records = reader.rows()
            first = next(records)
            maximum = reader.maximum
            append(store, 'book', 'pas:BTC', book('pas:BTC', START+999999), START+1000000, 'ready', START+999999)
            result = store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            self.assertEqual(result[0], 0)  # no batch cursor survives yield
            rows = [first, *records]
            self.assertEqual(len(rows), maximum)
            self.assertEqual(max(r['seq'] for r in rows), maximum)

    def test_live_wal_writer_during_streamed_export(self):
        ready, start, first_written = threading.Event(), threading.Event(), threading.Event()
        failures = []
        def writer():
            try:
                with closing(Capture(self.raw, 'pas')) as store:
                    ready.set()
                    if not start.wait(5):
                        raise TimeoutError('Export did not start')
                    for i in range(20):
                        stamp = START + 1_000_000 + i*1000
                        append(store, 'book', 'pas:BTC', book('pas:BTC', stamp-500), stamp,
                               'ready', stamp-500)
                        first_written.set()
                        time.sleep(0.002)
                    self.assertEqual(store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0], 0)
            except Exception as exc:
                failures.append(exc)
                ready.set()
                first_written.set()
        class SlowOutput(io.StringIO):
            def write(self, value):
                start.set()
                if not first_written.wait(5):
                    raise TimeoutError('Concurrent writer did not append')
                time.sleep(0.001)
                return super().write(value)
        with closing(research.readonly(self.raw)) as db:
            original = db.execute('SELECT count(*) FROM observations').fetchone()[0]
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            output = SlowOutput()
            export.export(self.raw, export.arguments(['--all']), output)
        finally:
            start.set()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures, failures)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(output.getvalue())))), original)
        self.assertEqual(len(self.rows('export --all')), original + 20)

    def test_reject_shell_paths_sql_abbreviations_duplicates_and_invalid_dates(self):
        bad = ['', 'sh', 'export --all; id', 'export --all | cat', 'export $(id)',
               'export --all > x', 'export --all\nid', 'export --database /etc/passwd --all',
               'export --all --sql SELECT', 'export --al', 'export --all --all',
               'export --all --symbol=BTC', 'export --all --symbol par:BTC',
               'export --all --from 2026-10-19T00:00:00Z',
               'export --from 2026-10-19 --to 2026-10-20',
               'export --from 2026-10-19T00:01:00Z --to 2026-10-19T00:00:00Z',
               'export --all --raw-json --dataset funding']
        for command in bad:
            with self.subTest(command=command), self.assertRaises((ValueError, SystemExit)):
                with patch('sys.stderr', io.StringIO()):
                    export.ssh_arguments(command)

    def test_empty_foreign_missing_and_timeout(self):
        empty = Path(self.tmp.name) / 'empty.sqlite3'
        Capture(empty, 'pas').close()
        for dataset in ('observations', 'funding', 'minute-panel'):
            output = io.StringIO()
            export.export(empty, export.ssh_arguments(f'export --all --dataset {dataset}'), output)
            self.assertEqual(len(output.getvalue().splitlines()), 1)
        with self.assertRaises(sqlite3.Error):
            export.export(empty.parent / 'missing', export.arguments(['--all']), io.StringIO())
        with patch.object(export, 'QUERY_SECONDS', -1):
            with self.assertRaises(sqlite3.OperationalError):
                export.export(self.raw, export.arguments(['--all']), io.StringIO())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux flock/resource behavior')
    def test_linux_lock_and_limits(self):
        lock = Path(self.tmp.name) / 'export.lock'
        lock.touch(mode=0o444)
        script = (
            'import resource; from pathlib import Path; '
            f'from {PACKAGE} import pas_csv_export as e; '
            f'e.LOCK=Path({str(lock)!r}); '
            'fd=e.limited_session(); '
            'assert resource.getrlimit(resource.RLIMIT_CORE)==(0,0); '
            'assert resource.getrlimit(resource.RLIMIT_AS)==(512*1024**2,)*2; '
            'print("locked",flush=True); input()'
        )
        process = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            retry = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(retry.returncode, 0)
            self.assertIn('Another export is active', retry.stderr)
        finally:
            process.communicate('done\n', timeout=10)
        self.assertEqual(process.returncode, 0)
        # Exiting the process releases the lock even without deleting/recreating its inode.
        retry = subprocess.run([sys.executable, '-c', script], input='done\n',
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(retry.returncode, 0, retry.stderr)

    @unittest.skipUnless(sys.platform == 'linux', 'POSIX alarm behavior')
    def test_slow_stdout_cannot_hold_lock_forever(self):
        lock = Path(self.tmp.name) / 'slow.lock'
        lock.touch(mode=0o444)
        script = f"""
import os
from pathlib import Path
from {PACKAGE} import pas_csv_export as e
e.LOCK=Path({str(lock)!r})
e.SECONDS=1
os.environ['SSH_ORIGINAL_COMMAND']='export --all'
def flood(path,args,out):
    while True:
        out.write('x'*65536)
e.export=flood
raise SystemExit(e.main())
"""
        process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            self.assertNotEqual(process.wait(timeout=5), 0)
        finally:
            process.kill() if process.poll() is None else None
            _, errors = process.communicate(timeout=5)
        self.assertNotIn(b'SyntaxError', errors)

    def test_short_read_keeps_raw_bytes_unchanged_and_rejects_foreign(self):
        before = self.raw.read_bytes()
        self.rows('export --all')
        self.assertEqual(self.raw.read_bytes(), before)
        foreign = Path(self.tmp.name) / 'foreign.sqlite3'
        Capture(foreign, 'par').close()
        with self.assertRaises(ValueError):
            export.export(foreign, export.arguments(['--all']), io.StringIO())

    def test_failure_nonzero_stderr_and_no_csv(self):
        with patch.dict('os.environ', {'SSH_ORIGINAL_COMMAND': 'id'}), \
                patch('sys.stderr', io.StringIO()) as errors:
            self.assertEqual(export.main(), 1)
            self.assertIn('Only export', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
