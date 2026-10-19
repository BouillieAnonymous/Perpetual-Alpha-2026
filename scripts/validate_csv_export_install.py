"""Validate the installed runtime with -I, outside the repository import path."""
import importlib.metadata
import importlib.util
import json
import sqlite3
import sys

import alpha_csv_export.pas_csv_export as exporter

assert sys.version_info[:2] == (3, 14)
assert not importlib.metadata.requires('perpetual-alpha-csv-export')
for name in ('requests', 'hyperliquid', 'eth_account', 'perpetual_alpha'):
    assert importlib.util.find_spec(name) is None, f'Unexpected runtime dependency: {name}'
assert exporter.ssh_arguments('export --all').all
print(json.dumps({'python': sys.version, 'sqlite': sqlite3.sqlite_version,
                  'exporter': exporter.__file__, 'external_runtime_dependencies': []}))
