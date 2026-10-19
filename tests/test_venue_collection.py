import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from perpetual_alpha.venue_collection import Capture, Collector, book_quality, status
from perpetual_alpha.venues import venue


class Response:
    content = b'{}'

    def __init__(self, raw):
        self.raw = raw

    def raise_for_status(self):
        pass

    def json(self):
        return self.raw


class Session:
    def __init__(self):
        self.calls = []
        self.fail_coin = None

    def post(self, url, *, json, timeout):
        self.calls.append((url, json))
        if json['type'] == 'metaAndAssetCtxs':
            return Response([{'universe': [{'name': s} for s in venue('pas').symbols]},
                             [{'oraclePx': '100', 'markPx': '100', 'funding': '0.001'}] * 10])
        if json['type'] == 'fundingHistory':
            return Response([{'coin': json['coin'], 'time': 100_000, 'fundingRate': '0.001'}])
        if json['coin'] == self.fail_coin:
            raise TimeoutError('simulated public timeout')
        return Response({'coin': json['coin'], 'time': 100_000,
                         'levels': [[], []] if json['coin'] == 'pas:BTC' else
                         [[{'px': '99', 'sz': '1'}], [{'px': '101', 'sz': '1'}]]})


class VenueCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'public.sqlite3'
        self.store = Capture(self.path, 'pas')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_empty_book_is_retained_and_other_markets_continue(self):
        collector = Collector(self.store, session=Session(), clock=lambda: 100_000)
        collector.cycle()
        books = self.store.db.execute("SELECT symbol,quality FROM observations WHERE kind='book'").fetchall()
        self.assertEqual(len(books), 10)
        self.assertEqual(dict(books)['pas:BTC'], 'empty-or-one-sided')
        self.assertEqual(dict(books)['pas:ETH'], 'ready')
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM observations WHERE kind='context'").fetchone()[0], 10)

    def test_one_public_timeout_does_not_discard_other_assets(self):
        session = Session()
        session.fail_coin = 'pas:ETH'
        Collector(self.store, session=session, clock=lambda: 100_000).cycle()
        errors = self.store.db.execute("SELECT symbol FROM observations WHERE kind='request-error'").fetchall()
        self.assertEqual(errors, [('pas:ETH',)])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM observations WHERE kind='book'").fetchone()[0], 9)

    def test_same_book_receipts_and_new_contexts_not_deduplicated(self):
        collector = Collector(self.store, session=Session(), clock=lambda: 100_000)
        collector.cycle()
        collector.cycle()
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM observations WHERE kind='book'").fetchone()[0], 20)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM observations WHERE kind='context'").fetchone()[0], 20)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM observations WHERE kind='settled-funding'").fetchone()[0], 10)

    def test_no_wallet_or_signed_queries(self):
        session = Session()
        Collector(self.store, session=session, clock=lambda: 100_000).cycle()
        for url, query in session.calls:
            self.assertEqual(url, 'https://api.hyperliquid-testnet.xyz/info')
            self.assertIn(query['type'], {'metaAndAssetCtxs', 'l2Book', 'fundingHistory'})
            self.assertNotIn('user', query)

    def test_par_namespace_cannot_relabel_pas(self):
        with self.assertRaises(ValueError):
            Capture(self.path, 'par')
        with self.assertRaises(ValueError):
            self.store.append('book', 'par:ETH', {'type': 'l2Book', 'coin': 'par:ETH'}, {}, 1, 2, 'invalid')

    def test_foreign_database_never_adopted(self):
        path = Path(self.tmp.name) / 'foreign.sqlite3'
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE prior_data (value TEXT)')
        db.commit()
        db.close()
        with self.assertRaises(ValueError):
            Capture(path, 'pas')

    def test_restart_and_provenance(self):
        Collector(self.store, session=Session(), clock=lambda: 100_000).cycle()
        self.store.close()
        self.store = Capture(self.path, 'pas')
        identity = json.loads(self.store.db.execute('SELECT value FROM identity').fetchone()[0])
        self.assertEqual(identity['source'], 'hyperliquid-testnet/pas')
        self.assertEqual(identity['collateral'], 'USDP')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 31)
        report = status(self.path, 'pas')
        self.assertTrue(report['alive'])
        self.assertFalse(report['markets']['pas:BTC']['quote_ready'])
        self.assertFalse(report['signing'])

    def test_bad_books_never_ready(self):
        for book in [{}, {'coin': 'pas:ETH', 'time': 100_000, 'levels': [[], []]},
                     {'coin': 'par:ETH', 'time': 100_000, 'levels': [[], []]},
                     {'coin': 'pas:ETH', 'time': 100_001, 'levels': [[], []]}]:
            self.assertNotEqual(book_quality('pas:ETH', book, 100_000), 'ready')
