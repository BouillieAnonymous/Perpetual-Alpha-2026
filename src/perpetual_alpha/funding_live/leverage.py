"""Separately approved PAS 1x Cross setup; never submits an order."""
import argparse
import json
from pathlib import Path

from .activation import require_approval
from .policy import load_policy
from .preparation import preflight
from .venue import TestnetVenue, millis


def configure(client, policy, approval_file, par_wallet_file):
    if policy.dex != 'pas' or policy.enabled:
        raise ValueError('Separate leverage setup requires disabled PAS policy')
    client.action_guard = lambda purpose: require_approval(
        approval_file, purpose, policy, client, millis(), par_wallet_file)
    client.check_action_approval('leverage')
    evidence = preflight(client, policy, par_wallet_file)
    # Only the existing leverage discrepancy can be waived by this command.
    issues = [x for x in evidence['issues'] if not x.startswith('PAS ETH leverage is not verified')]
    if issues:
        raise ValueError('Read-only preflight failed; leverage operation forbidden')
    client.enable_signer(scope='leverage-only')
    response = client.set_leverage()
    after = client.query('activeAssetData', user=client.team, coin=policy.symbol)
    state = client.query('clearinghouseState', user=client.team, dex='pas')
    if after.get('leverage') != {'type': 'cross', 'value': 1}:
        raise ValueError('Exchange did not confirm 1x Cross; do not activate')
    if any(x['position']['leverage'] != {'type': 'cross', 'value': 1}
           for x in state['assetPositions'] if x['position']['coin'] == policy.symbol):
        raise ValueError('Position leverage confirmation mismatch')
    return {'schema': 'pas-leverage-confirmation/1', 'time_ms': millis(),
            'response': response, 'active': after, 'clearinghouse': state,
            'orders_submitted': 0, 'trading_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--approval-file', type=Path, required=True)
    parser.add_argument('--par-wallet-file', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if args.evidence.exists():
        raise ValueError('Evidence destination must be a new file')
    p = load_policy(args.config)
    client = TestnetVenue(p, args.env_file)
    try:
        result = configure(client, p, args.approval_file, args.par_wallet_file)
        with args.evidence.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps({'confirmed': True, 'orders_submitted': 0, 'evidence': str(args.evidence)}))
    finally:
        client.close()


if __name__ == '__main__':
    main()
