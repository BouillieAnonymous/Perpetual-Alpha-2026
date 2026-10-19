"""Build a deterministic, stdlib-only exporter wheel without installing the trading project."""
import argparse
import ast
import base64
import csv
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'alpha_csv_export'
VERSION = '0.1.0'
DIST = f'perpetual_alpha_csv_export-{VERSION}.dist-info'
WHEEL_NAME = f'perpetual_alpha_csv_export-{VERSION}-py3-none-any.whl'


def payload():
    source = ROOT / 'src/perpetual_alpha'
    files = {f'{PACKAGE}/__init__.py': b'"""Independent stdlib-only PAS CSV exporter runtime."""\n'}
    manifest = {}
    for name in ('pas_csv_export.py', 'pas_research.py', 'venues.py', 'venue_collection.py'):
        text = (source / name).read_text(encoding='utf-8')
        manifest[f'src/perpetual_alpha/{name}'] = hashlib.sha256(text.encode()).hexdigest()
        if name != 'venue_collection.py':
            files[f'{PACKAGE}/{name}'] = text.encode()
        else:
            names = {'APPLICATION_ID', 'URL', 'canonical', 'book_quality'}
            selected = {}
            for node in ast.parse(text).body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    key = getattr(node.targets[0], 'id', '')
                else:
                    key = getattr(node, 'name', '')
                if key in names:
                    selected[key] = ast.get_source_segment(text, node)
            if selected.keys() != names:
                raise ValueError('Collector helper extraction changed; review dependency boundary')
            helpers = '"""Generated exact collector helpers; no collector, HTTP client or SDK."""\nimport json\n\n'
            helpers += '\n\n'.join(selected.values()) + '\n'
            files[f'{PACKAGE}/venue_collection.py'] = helpers.encode()
    # Fail closed if a future source change pulls non-stdlib or unbundled modules into the runtime.
    for name, data in files.items():
        for node in ast.walk(ast.parse(data)):
            if isinstance(node, ast.Import):
                imports = [alias.name.split('.')[0] for alias in node.names]
                if any(module not in sys.stdlib_module_names for module in imports):
                    raise ValueError(f'Non-stdlib dependency in {name}: {imports}')
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or '').split('.')[0]
                if node.level:
                    modules = [module] if module else [alias.name for alias in node.names]
                    if node.level != 1 or any(f'{PACKAGE}/{item}.py' not in files for item in modules):
                        raise ValueError(f'Unbundled relative import in {name}: {node.module}')
                elif module not in sys.stdlib_module_names:
                    raise ValueError(f'Non-stdlib dependency in {name}: {node.module}')
    files[f'{PACKAGE}/source_manifest.json'] = (json.dumps(manifest, sort_keys=True, indent=2)+'\n').encode()
    files[f'{DIST}/METADATA'] = (
        'Metadata-Version: 2.1\nName: perpetual-alpha-csv-export\n'
        f'Version: {VERSION}\nSummary: Independent read-only PAS CSV export\n'
        'Requires-Python: >=3.14,<3.15\n\n'
    ).encode()
    files[f'{DIST}/WHEEL'] = (
        'Wheel-Version: 1.0\nGenerator: alpha-csv-stdlib-builder\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
    ).encode()
    return files


def build(directory):
    files = payload()
    record = io.StringIO(newline='')
    writer = csv.writer(record, lineterminator='\n')
    for name, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow((name, 'sha256='+digest, len(data)))
    writer.writerow((f'{DIST}/RECORD', '', ''))
    files[f'{DIST}/RECORD'] = record.getvalue().encode()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / WHEEL_NAME
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    target = build(parser.parse_args().output)
    print(json.dumps({'wheel': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
