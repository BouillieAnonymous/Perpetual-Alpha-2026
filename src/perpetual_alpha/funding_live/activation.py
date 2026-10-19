"""PAS operator approvals are separate for leverage configuration and trading."""
import json
from pathlib import Path

from ..api_client import address


def exclusive_wallet(client, par_wallet_file):
    if not par_wallet_file or not Path(par_wallet_file).is_file():
        raise ValueError('PAR signer identity must be supplied for PAS isolation')
    if address(Path(par_wallet_file).read_text().strip()) == address(client.settings['API_WALLET_ADDRESS']):
        raise ValueError('PAS requires a separate authorized API wallet; current wallet is used by PAR')


def require_approval(path, purpose, policy, client, now_ms, par_wallet_file):
    if policy.dex != 'pas' or purpose not in {'leverage', 'trading'}:
        raise ValueError('Explicit PAS approval purpose required')
    if not path or not Path(path).is_file():
        raise ValueError('PAS operator approval file absent; no signing authority')
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    expected = {'schema', 'purpose', 'dex', 'team_wallet', 'api_wallet', 'policy_hash',
                'not_before_ms', 'expires_ms'}
    if set(value) != expected or value['schema'] != 'pas-operator-approval/1':
        raise ValueError('Unsupported approval fields/schema')
    if (value['purpose'] != purpose or value['dex'] != 'pas'
            or address(value['team_wallet']) != client.team
            or address(value['api_wallet']) != address(client.settings['API_WALLET_ADDRESS'])
            or value['policy_hash'] != policy.digest):
        raise ValueError('Approval purpose/account/policy mismatch')
    start, end = value['not_before_ms'], value['expires_ms']
    if (type(start) is not int or type(end) is not int
            or not 0 < end-start <= 86_400_000 or not start <= now_ms < end):
        raise ValueError('Approval expired, future-dated or exceeds 24h')
    exclusive_wallet(client, par_wallet_file)
    return value
