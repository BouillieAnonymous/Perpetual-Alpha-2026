"""Environment isolation without migrating data or enabling competition services."""
import json
import tempfile
import unittest
from pathlib import Path

from perpetual_alpha.config import settings as collector_settings
from perpetual_alpha.systems import describe, initialize, load_system
from perpetual_alpha.venues import common_venue, source_venue, symbol_venue

ROOT = Path(__file__).resolve().parents[1]


class SystemSeparationTests(unittest.TestCase):
    def profile(self, dex):
        return load_system(ROOT/'config/systems'/f'{dex}.toml')

    def test_all_paths_and_symbols_are_disjoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            par = initialize(self.profile('par'), tmp)
            pas = initialize(self.profile('pas'), tmp)
            self.assertFalse(set(par['paths'].values()) & set(pas['paths'].values()))
            self.assertFalse(set(par['symbols']) & set(pas['symbols']))
            self.assertNotEqual(par['collateral'], pas['collateral'])
            self.assertFalse(pas['collector_enabled'])
            self.assertFalse(pas['trading_enabled'])
            self.assertFalse((Path(pas['root'])/'collector.toml').exists())
            parsed = collector_settings(par['collector_config'])
            self.assertEqual(parsed['symbols'], par['symbols'])
            self.assertEqual(parsed['database'], par['paths']['quotes'])

    def test_reserved_pas_cannot_be_activated_or_mislabelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            for changes in [{'collector_enabled': True}, {'trading_enabled': True},
                            {'source': 'hyperliquid-testnet/par'}, {'collateral': 'USDPR'}]:
                with self.assertRaises(ValueError):
                    initialize({**self.profile('pas'), **changes}, tmp)

    def test_existing_legacy_data_not_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'pas'
            folder.mkdir()
            original = folder/'history.sqlite3'
            original.write_bytes(b'legacy bytes')
            with self.assertRaises(ValueError):
                initialize(self.profile('pas'), tmp)
            self.assertEqual(original.read_bytes(), b'legacy bytes')
            self.assertFalse((folder/'system-identity.json').exists())

    def test_init_is_idempotent_and_rejects_changed_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = initialize(self.profile('par'), tmp)
            self.assertEqual(first, initialize(self.profile('par'), tmp))
            marker = Path(first['root'])/'system-identity.json'
            original = marker.read_text()
            marker.write_text(json.dumps({'system_id': 'hyperliquid-testnet/pas'}))
            with self.assertRaises(ValueError):
                initialize(self.profile('par'), tmp)
            self.assertNotEqual(marker.read_text(), original)

    def test_inspect_does_not_create_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            describe(self.profile('pas'), tmp)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_mixed_venue_never_becomes_one_source(self):
        with self.assertRaises(ValueError):
            common_venue(['par:BTC', 'pas:ETH'])
        self.assertEqual(symbol_venue('par:HYPE'), source_venue('hyperliquid-testnet/par'))
        for symbol in ['BTC', 'xyz:EWY', 'pas:UNKNOWN']:
            with self.assertRaises(ValueError):
                symbol_venue(symbol)
