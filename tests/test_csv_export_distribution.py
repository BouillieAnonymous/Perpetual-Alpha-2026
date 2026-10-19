"""Wheel boundary tests run under both supported main runtime and Python 3.14."""
import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('builder', ROOT / 'scripts/build_csv_export_wheel.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class DistributionTests(unittest.TestCase):
    def test_reproducible_wheel_and_record_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            first = builder.build(Path(directory) / 'a').read_bytes()
            second = builder.build(Path(directory) / 'b').read_bytes()
            self.assertEqual(first, second)
            with zipfile.ZipFile(io.BytesIO(first)) as wheel:
                metadata = wheel.read(f'{builder.DIST}/METADATA').decode()
                self.assertIn('Requires-Python: >=3.14,<3.15', metadata)
                self.assertNotIn('Requires-Dist:', metadata)
                record = wheel.read(f'{builder.DIST}/RECORD').decode()
                for name, digest, size in csv.reader(io.StringIO(record)):
                    if not digest:
                        self.assertEqual(name, f'{builder.DIST}/RECORD')
                        continue
                    data = wheel.read(name)
                    self.assertEqual(int(size), len(data))
                    actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
                    self.assertEqual(digest, 'sha256='+actual)

    def test_exact_shared_sources_and_no_collector_runtime(self):
        files = builder.payload()
        for name in ('pas_csv_export.py', 'pas_research.py', 'venues.py'):
            self.assertEqual(files[f'alpha_csv_export/{name}'],
                             (ROOT / 'src/perpetual_alpha' / name).read_text(encoding='utf-8').encode())
        helper = files['alpha_csv_export/venue_collection.py'].decode()
        self.assertNotIn('class Capture', helper)
        self.assertNotIn('requests', helper)
        self.assertNotIn('ProcessLock', helper)
        manifest = json.loads(files['alpha_csv_export/source_manifest.json'])
        self.assertEqual(len(manifest), 4)
        with (ROOT / 'pyproject.toml').open('rb') as stream:
            self.assertEqual(tomllib.load(stream)['project']['requires-python'], '>=3.11,<3.14')

    def test_fail_closed_on_new_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'src/perpetual_alpha'
            source.mkdir(parents=True)
            for name in ('pas_csv_export.py', 'pas_research.py', 'venues.py', 'venue_collection.py'):
                shutil.copyfile(ROOT / 'src/perpetual_alpha' / name, source / name)
            path = source / 'pas_csv_export.py'
            original = path.read_text(encoding='utf-8')
            for unexpected in ('import requests', 'from .private_keys import wallet'):
                path.write_text(original+'\n'+unexpected+'\n', encoding='utf-8')
                with patch.object(builder, 'ROOT', root), self.assertRaises(ValueError):
                    builder.payload()

    @unittest.skipUnless(os.environ.get('CSV_EXPORT_TEST_PYTHON'), 'Standalone runtime checked in Python 3.14 CI')
    def test_installed_venv_without_source_or_external_dependencies(self):
        result = subprocess.run([os.environ['CSV_EXPORT_TEST_PYTHON'], '-I',
                                 str(ROOT / 'scripts/validate_csv_export_install.py')],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['external_runtime_dependencies'], [])


if __name__ == '__main__':
    unittest.main()
