import hashlib
import json
import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from perpetual_alpha import pas_research as research
from perpetual_alpha.examples.pas_research_fixture import START, append, book, create
from perpetual_alpha.venue_collection import Capture


class PASResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = self.root/'public.sqlite3'
        self.derived = self.root/'research.sqlite3'
        self.store = Capture(self.raw, 'pas')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add_book(self, t, mid=100, event=None, **kwargs):
        event = t-500 if event is None else event
        append(self.store, 'book', 'pas:BTC', book('pas:BTC', event, mid), t, 'ready', event, **kwargs)

    def add_context(self, t, oracle=100):
        append(self.store, 'context', 'pas:BTC',
               {'context': {'markPx': '101', 'oraclePx': str(oracle), 'funding': '0.001'}}, t)

    def points(self, start=START, end=START+4*60_000):
        with closing(research.readonly(self.derived)) as db:
            return [r for r in research.panel(db, start, end) if r['symbol'] == 'pas:BTC']

    def test_readonly_source_wal_and_unchanged_bytes(self):
        self.add_book(START)
        self.add_context(START)
        files = research.database_files(self.raw)
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in files if p.exists() and not str(p).endswith('-shm')}
        reader = research.readonly(self.raw)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                reader.execute('DELETE FROM observations')
        finally:
            reader.close()
        research.ingest(self.raw, self.derived)
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in files if p.exists() and not str(p).endswith('-shm')})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 2)

    def test_received_and_storage_cutoffs_no_future_context(self):
        self.add_book(START-1000, stored=START+1)
        self.add_context(START+10, 50)
        research.ingest(self.raw, self.derived)
        points = self.points()
        self.assertFalse(points[0]['quote_valid'])  # received, but not yet stored
        self.assertTrue(points[1]['quote_valid'])
        self.assertEqual(points[1]['oracle'], 50)
        prefix = points[:2]
        self.add_context(START+90_000, 1)
        self.add_book(START+90_000, 500)
        research.ingest(self.raw, self.derived)
        self.assertEqual(self.points()[:2], prefix)

    def test_future_oracle_never_backfills_and_times_independent(self):
        self.add_book(START-1000)
        self.add_context(START+1000)
        research.ingest(self.raw, self.derived)
        row = self.points()[0]
        self.assertEqual(row['mid'], 100)
        self.assertIsNone(row['oracle'])
        self.assertIsNone(row['basis_bps'])
        self.assertNotEqual(self.points()[1]['book_received_ms'], self.points()[1]['context_received_ms'])

    def test_empty_crossed_zero_size_nan_and_stale_never_prices(self):
        variants = [book('pas:BTC', START-1000, empty=True),
                    book('pas:BTC', START-1000), book('pas:BTC', START-1000),
                    book('pas:BTC', START-1000), book('pas:BTC', START-100_000)]
        variants[1]['levels'][0][0]['px'] = '999'
        variants[2]['levels'][0][0]['sz'] = '0'
        variants[3]['levels'][0][0]['px'] = 'nan'
        for i, raw in enumerate(variants):
            t = START+i*60_000-1000
            if i < 4:
                raw['time'] = t-500
            append(self.store, 'book', 'pas:BTC', raw, t, 'ready', raw['time'])
        research.ingest(self.raw, self.derived)
        for row in self.points(end=START+5*60_000):
            self.assertFalse(row['quote_valid'])
            self.assertIsNone(row['bid'])
            self.assertIsNone(row['mid'])

    def test_gap_and_repeated_book_do_not_fabricate_returns(self):
        self.add_book(START-1000)
        self.add_book(START+59_000, 102, event=START-1500)
        self.add_book(START+239_000, 103)
        research.ingest(self.raw, self.derived)
        rows = self.points(end=START+5*60_000)
        self.assertTrue(rows[1]['quote_valid'])
        self.assertIsNone(rows[1]['log_return'])
        self.assertFalse(rows[2]['quote_valid'])
        self.assertIsNone(rows[4]['log_return'])

    def test_request_error_invalidates_previous_quote(self):
        self.add_book(START-1000)
        append(self.store, 'request-error', 'pas:BTC', {'error': 'TimeoutError'}, START+50_000, 'request-error')
        research.ingest(self.raw, self.derived)
        self.assertFalse(self.points()[1]['quote_valid'])

    def test_incremental_idempotence_and_restart_after_committed_batch(self):
        self.add_book(START-1000)
        self.add_context(START-1000)
        original = research.normalize
        calls = 0
        def crash(row):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('simulated interruption')
            return original(row)
        with patch.object(research, 'normalize', crash):
            with self.assertRaises(RuntimeError):
                research.ingest(self.raw, self.derived, batch_size=1)
        result = research.ingest(self.raw, self.derived)
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(research.ingest(self.raw, self.derived)['inserted'], 0)
        rebuilt = self.root/'rebuilt.sqlite3'
        research.ingest(self.raw, rebuilt)
        with closing(research.readonly(self.derived)) as one, closing(research.readonly(rebuilt)) as two:
            self.assertEqual([tuple(r) for r in one.execute('SELECT * FROM samples ORDER BY seq')],
                             [tuple(r) for r in two.execute('SELECT * FROM samples ORDER BY seq')])

    def test_uncommitted_batch_rolls_back(self):
        self.add_book(START-1000)
        self.add_context(START-1000)
        original = research.normalize
        def crash(row):
            if row['seq'] == 2:
                raise RuntimeError('interruption')
            return original(row)
        with patch.object(research, 'normalize', side_effect=crash):
            with self.assertRaises(RuntimeError):
                research.ingest(self.raw, self.derived)
        self.assertEqual(research.ingest(self.raw, self.derived)['inserted'], 2)

    def test_changed_raw_prefix_rejected_without_progress(self):
        self.add_book(START-1000)
        research.ingest(self.raw, self.derived)
        self.store.db.execute("UPDATE observations SET quality='changed' WHERE seq=1")
        with self.assertRaisesRegex(ValueError, 'prefix changed'):
            research.ingest(self.raw, self.derived)

    def test_par_identity_and_symbols_rejected(self):
        identity = self.store.db.execute('SELECT value FROM identity').fetchone()[0]
        changed = json.loads(identity)
        changed['source'] = 'hyperliquid-testnet/par'
        self.store.db.execute('UPDATE identity SET value=?', (json.dumps(changed),))
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            research.ingest(self.raw, self.derived)
        self.assertFalse(self.derived.exists())
        self.store.db.execute('UPDATE identity SET value=?', (identity,))
        self.add_book(START-1000)
        self.store.db.execute("UPDATE observations SET symbol='par:BTC'")
        with self.assertRaisesRegex(ValueError, 'Non-PAS'):
            research.ingest(self.raw, self.derived)

    def test_foreign_or_aliased_outputs_rejected(self):
        with self.assertRaises(ValueError):
            research.ingest(self.raw, self.raw)
        with self.assertRaises(ValueError):
            research.ingest(self.raw, str(self.raw)+'-wal')
        db = sqlite3.connect(self.derived)
        db.execute('CREATE TABLE important(value TEXT)')
        db.commit()
        db.close()
        with self.assertRaisesRegex(ValueError, 'Foreign'):
            research.ingest(self.raw, self.derived)

    def test_payload_corruption_and_truncation_rejected(self):
        self.add_book(START-1000)
        self.store.db.execute("UPDATE observations SET raw_json='{}'")
        with self.assertRaisesRegex(ValueError, 'checksum'):
            research.ingest(self.raw, self.derived)
        self.store.db.execute('DELETE FROM observations')
        self.add_book(START-1000)
        research.ingest(self.raw, self.derived)
        self.store.db.execute('DELETE FROM observations')
        with self.assertRaisesRegex(ValueError, 'prefix changed'):
            research.ingest(self.raw, self.derived)

    def test_malformed_context_and_future_settlement_remain_unknown(self):
        append(self.store, 'context', 'pas:BTC', {'context': []}, START-1000)
        append(self.store, 'settled-funding', 'pas:BTC',
               [{'coin': 'pas:BTC', 'time': START+3_600_000, 'fundingRate': '0.001'}], START-1000)
        research.ingest(self.raw, self.derived)
        point = self.points()[0]
        self.assertIsNone(point['oracle'])
        self.assertIsNone(point['funding_rate'])

    def test_report_cannot_overwrite_raw_alias(self):
        # A raw input is allowed to have a misleading CSV suffix; export still must not overwrite it.
        special = self.root/'2026-10-19-panel.csv'
        store = Capture(special, 'pas')
        store.close()
        research.ingest(special, self.derived)
        before = special.read_bytes()
        with self.assertRaisesRegex(ValueError, 'aliases the raw'):
            research.report(self.derived, '2026-10-19', self.root)
        self.assertEqual(special.read_bytes(), before)

    def test_code_change_and_different_input_require_rebuild(self):
        research.ingest(self.raw, self.derived)
        with closing(sqlite3.connect(self.derived)) as db:
            db.execute("UPDATE state SET pipeline_hash='older algorithm'")
            db.commit()
        with self.assertRaisesRegex(ValueError, 'code changed'):
            research.ingest(self.raw, self.derived)
        other = self.root/'different.sqlite3'
        store = Capture(other, 'pas')
        store.close()
        with self.assertRaisesRegex(ValueError, 'binding mismatch'):
            research.ingest(other, self.derived)

    def test_funding_overlap_revision_availability_and_change(self):
        symbol = 'pas:BTC'
        funding = [{'coin': symbol, 'time': START-3_600_000, 'fundingRate': '0.0001'},
                   {'coin': symbol, 'time': START, 'fundingRate': '0.0002'}]
        append(self.store, 'settled-funding', symbol, funding, START+1000)
        append(self.store, 'settled-funding', symbol, funding, START+2000)
        research.ingest(self.raw, self.derived)
        self.assertIsNone(self.points()[0]['funding_rate'])
        self.assertAlmostEqual(self.points()[1]['funding_change'], 0.0001)
        before = self.points()[:2]
        funding[1]['fundingRate'] = '0.0009'
        append(self.store, 'settled-funding', symbol, funding, START+90_000)
        research.ingest(self.raw, self.derived)
        self.assertEqual(self.points()[:2], before)
        self.assertEqual(self.points()[2]['funding_rate'], 0.0009)
        with closing(research.readonly(self.derived)) as db:
            self.assertEqual(research.summarize(db, START, START+research.DAY)[0]
                             ['unique_settled_funding_events'], 1)

    def test_returns_and_statistics_hand_calculation(self):
        for i, mid in enumerate((100, 101, 100)):
            self.add_context(START+i*60_000-1500, 100)
            self.add_book(START+i*60_000-1000, mid)
        research.ingest(self.raw, self.derived)
        rows = self.points()
        self.assertAlmostEqual(rows[0]['spread_bps'], 20)
        self.assertAlmostEqual(rows[1]['basis_bps'], 100)
        self.assertAlmostEqual(rows[1]['log_return'], math.log(1.01))
        with closing(research.readonly(self.derived)) as db:
            stat = research.summarize(db, START, START+research.DAY)[0]
            self.assertEqual(stat['return_samples'], 2)
            self.assertEqual(stat['grid_points'], 1440)
            self.assertEqual(stat['valid_quotes'], 4)  # final quote remains inside the explicit 90s TTL

    def test_daily_example_reproducible_and_all_ten_assets(self):
        fixture = self.root/'synthetic.sqlite3'
        create(fixture)
        research.ingest(fixture, self.derived)
        out = self.root/'reports'
        stats = research.report(self.derived, '2026-10-19', out)
        first = {p.name: p.read_bytes() for p in out.iterdir()}
        research.report(self.derived, '2026-10-19', out)
        self.assertEqual(first, {p.name: p.read_bytes() for p in out.iterdir()})
        self.assertEqual(len(stats), 10)
        self.assertEqual(stats[-1]['valid_quotes'], 0)
        self.assertIsNone(stats[-1]['minute_volatility'])

    def test_committed_synthetic_statistics_and_report(self):
        fixture = self.root/'fixture.sqlite3'
        create(fixture)
        research.ingest(fixture, self.derived)
        out = self.root/'golden'
        research.report(self.derived, '2026-10-19', out, 'SYNTHETIC')
        expected = Path(__file__).resolve().parents[1]/'docs/research/pas-pipeline-example'
        for suffix in ('statistics.csv', 'report.md'):
            name = '2026-10-19-'+suffix
            self.assertEqual((out/name).read_text(encoding='utf-8'),
                             (expected/name).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
