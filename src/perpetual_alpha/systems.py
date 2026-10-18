"""Separate PAR/PAS workspaces; no deployment, collection or signing authority.

PAR keeps its existing runtime. PAS reserves an independent namespace and is not
presented as a functioning competition adapter before implementation/activation.
"""
import argparse
import json
import os
import tomllib
from pathlib import Path

from .venues import venue


def load_system(path):
    with Path(path).open('rb') as stream:
        settings = tomllib.load(stream)['system']
    return validate_system(settings)


def validate_system(settings):
    expected = {'dex', 'source', 'collateral', 'mode', 'collector_enabled', 'trading_enabled'}
    if set(settings) != expected:
        raise ValueError('Unexpected system settings')
    selected = venue(settings['dex'])
    if settings['source'] != selected.source or settings['collateral'] != selected.collateral:
        raise ValueError('Source/dex/collateral mismatch')
    mode = 'rehearsal' if selected.dex == 'par' else 'competition-reserved'
    if settings['mode'] != mode or type(settings['collector_enabled']) is not bool:
        raise ValueError('Invalid system mode')
    if settings['trading_enabled'] is not False:
        raise ValueError('System separation is not permission to trade')
    if selected.dex == 'pas' and settings['collector_enabled']:
        raise ValueError('PAS is reserved; no automatic collector activation')
    return settings


def describe(settings, root):
    settings = validate_system(settings)
    selected = venue(settings['dex'])
    base = Path(root).resolve()
    folder = base/selected.dex
    if folder.is_symlink() or folder.resolve() != folder:
        raise ValueError('Environment directory cannot alias another namespace')
    names = {'quotes': 'market.sqlite3', 'history': 'history.sqlite3', 'account': 'account.sqlite3',
             'runtime': 'runtime.sqlite3', 'paper': 'paper.sqlite3', 'journal': 'journal.sqlite3',
             'logs': 'logs', 'research': 'research'}
    for name in [*names.values(), 'system-identity.json', 'collector.toml']:
        path = folder/name
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('System paths cannot alias another directory')
    result = {**settings, 'symbols': list(selected.symbols), 'root': str(folder),
              'paths': {key: str(folder/value) for key, value in names.items()},
              'system_id': selected.source, 'risk_ready': False, 'deployed': False,
              'pas_runtime_status': 'RESERVED_NOT_IMPLEMENTED' if selected.dex == 'pas' else 'NOT_APPLICABLE'}
    identity = folder/'system-identity.json'
    if identity.exists():
        actual = json.loads(identity.read_text(encoding='utf-8'))
        if actual != {'system_id': selected.source, 'collateral': selected.collateral, 'schema': 'system-identity/1'}:
            raise ValueError('Stored environment identity mismatch; never relabel existing data')
    return result


def initialize(settings, root):
    result = describe(settings, root)
    folder = Path(result['root'])
    if folder.exists() and not (folder/'system-identity.json').exists() and any(folder.iterdir()):
        raise ValueError('Preserve existing unlabelled directory; do not adopt it')
    folder.mkdir(parents=True, exist_ok=True)
    selected = venue(settings['dex'])
    identity = {'system_id': selected.source, 'collateral': selected.collateral, 'schema': 'system-identity/1'}
    target = folder/'system-identity.json'
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        describe(settings, root)
    else:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(identity, indent=2)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
    for name in ('logs', 'research'):
        (folder/name).mkdir(exist_ok=True)
    if selected.dex == 'par':
        config = folder/'collector.toml'
        expected = ('[collector]\n# Explicit rehearsal-only collector; no trading.\n'
                    'symbols = '+json.dumps(list(selected.symbols))+'\n'
                    'database = '+json.dumps(result['paths']['quotes'])+'\n'
                    'poll_seconds = 5\nstale_seconds = 90\n')
        if config.exists() and config.read_text(encoding='utf-8') != expected:
            raise ValueError('Existing collector config differs; refusing to overwrite')
        if not config.exists():
            with config.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(expected)
        result['collector_config'] = str(config)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inspect', 'init'))
    parser.add_argument('--system', choices=('par', 'pas'), required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    config = Path(__file__).resolve().parents[2]/'config/systems'/f'{args.system}.toml'
    settings = load_system(config)
    result = initialize(settings, args.root) if args.command == 'init' else describe(settings, args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
