"""Read-only capture and lazy signer, fixed Hyperliquid testnet URL only."""
import os
import time
from pathlib import Path

import requests

from ..api_client import TESTNET_URL, address, load_public_settings
from .policy import HOUR, dec


def millis():
    return int(time.time() * 1000)


class TestnetVenue:
    def __init__(self, policy, env_path='.env', *, session=None, clock=millis):
        self.policy = policy
        self.settings = load_public_settings(env_path)
        self.team = address(self.settings['TEAM_WALLET_ADDRESS'])
        self.env_path = Path(env_path)
        self.session = session or requests.Session()
        self.clock = clock
        self.exchange = None
        self.signer_address = None

    def query(self, kind, **fields):
        # No retry on signed actions. Public queries also have bounded I/O.
        response = self.session.post(TESTNET_URL + '/info', json={'type': kind, **fields}, timeout=(5, 15))
        response.raise_for_status()
        if len(response.content) > 8_000_000:
            raise ValueError('Oversize info response')
        return response.json()

    def snapshot(self):
        p = self.policy
        started = self.clock()
        meta, contexts = self.query('metaAndAssetCtxs', dex=p.dex)
        universe = meta['universe']
        if len(universe) != len(contexts) or len({m['name'] for m in universe}) != len(universe):
            raise ValueError('Invalid market/context mapping')
        index = next(i for i, m in enumerate(universe) if m['name'] == p.symbol)
        spec, ctx = universe[index], contexts[index]
        token = meta['collateralToken']
        tokens = self.query('spotMeta')['tokens']
        matching = [t for t in tokens if t['index'] == token and t['name'] == p.collateral]
        if len(matching) != 1:
            raise ValueError('Collateral metadata mismatch')
        # Check EVERY perp metadata for shared collateral. Do not assume PAR/PAS exclusivity.
        all_meta = self.query('allPerpMetas')
        shared = [m for m in all_meta if m.get('collateralToken') == token and m['universe']]
        selected = [m for m in shared if m['universe'] == universe]
        if len(selected) != 1:
            raise ValueError('Selected DEX not uniquely present in full collateral scope')
        siblings = {}
        for other_meta in shared:
            if other_meta is selected[0]:
                continue
            names = {x['name'].split(':', 1)[0] if ':' in x['name'] else '' for x in other_meta['universe']}
            if len(names) != 1:
                raise ValueError('Ambiguous shared collateral DEX identity')
            dex = names.pop()
            siblings[dex] = {'state': self.query('clearinghouseState', user=self.team, dex=dex),
                             'orders': self.query('openOrders', user=self.team, dex=dex)}
        before = self.query('clearinghouseState', user=self.team, dex=p.dex)
        spot = self.query('spotClearinghouseState', user=self.team)
        mode = self.query('userAbstraction', user=self.team)
        orders = self.query('openOrders', user=self.team, dex=p.dex)
        spot_orders = self.query('openOrders', user=self.team, dex='')
        active = self.query('activeAssetData', user=self.team, coin=p.symbol)
        if active['coin'] != p.symbol or address(active['user']) != self.team:
            raise ValueError('Wrong active asset/account')
        fees = self.query('userFees', user=self.team)
        funding = self.query('fundingHistory', coin=p.symbol, startTime=started - 3 * HOUR, endTime=started)
        available = self.clock()
        for row in funding:
            row['available_ms'] = available
        # Account cashflow is archived, never substituted with a historical proxy.
        payments = self.query('userFunding', user=self.team, startTime=started - 24 * HOUR, endTime=started)
        fills = self.query('userFillsByTime', user=self.team, startTime=started - 24 * HOUR,
                           endTime=started, aggregateByTime=False)
        after = self.query('clearinghouseState', user=self.team, dex=p.dex)
        spot_after = self.query('spotClearinghouseState', user=self.team)
        book = self.query('l2Book', coin=p.symbol)
        received = self.clock()
        if book['coin'] != p.symbol or len(book['levels']) != 2 or not all(book['levels']):
            raise ValueError('Wrong/incomplete book')
        balances = [b for b in spot_after['balances'] if b['coin'] == p.collateral and b['token'] == token]
        balance = balances[0] if len(balances) == 1 else {'total': '0', 'hold': '0'}
        free = [x[1] for x in spot_after.get('tokenToAvailableAfterMaintenance', []) if x[0] == token]
        free_value = free[0] if len(free) == 1 else '0'
        positions = after['assetPositions']
        selected = [x['position'] for x in positions if x['position']['coin'] == p.symbol]
        other = [x['position']['coin'] for x in positions if x['position']['coin'] != p.symbol]
        for sibling in siblings.values():
            other.extend(x['position']['coin'] for x in sibling['state']['assetPositions'])
            orders.extend(sibling['orders'])
        if len(selected) > 1:
            raise ValueError('Duplicate position')
        quantity = selected[0]['szi'] if selected else '0'
        before_balances = [b for b in spot['balances'] if b['coin'] == p.collateral and b['token'] == token]
        before_total = dec(before_balances[0]['total']) if len(before_balances) == 1 else dec(0)
        # Unified spot total ALREADY includes unrealized PnL. Never add per-DEX PnL again.
        # Use the smaller endpoint value for conservative sizing/loss checks.
        equity = min(dec(balance['total']), before_total)
        before_free = [x[1] for x in spot.get('tokenToAvailableAfterMaintenance', []) if x[0] == token]
        free_value = str(min(dec(free_value), dec(before_free[0]) if len(before_free) == 1 else dec(0)))
        own_margin = max(
            sum((dec(x['position']['marginUsed']) for x in rows['assetPositions']
                 if x['position']['coin'] == p.symbol), dec(0)) for rows in (before, after)
        )
        token_unit = dec(10) ** -matching[0]['szDecimals']
        # A verified own cross position creates a spot hold. Only unexplained excess is foreign.
        if dec(balance['hold']) < 0 or dec(balance['hold']) > own_margin + token_unit:
            other.append('collateral-hold')
        # For this release only deployer scale 1/no growth is supported, fail on drift.
        if dec(spec.get('deployerFeeScale', '-1')) != 1 or meta.get('growthMode', False):
            raise ValueError('Unverified deployer fee model')
        fee_bps = dec(fees['userCrossRate']) * 2 * 10000
        raw = {'metadata': meta, 'context': ctx, 'spot_metadata': matching, 'scope': shared, 'siblings': siblings,
               'before': before, 'after': after, 'spot_before': spot, 'spot_after': spot_after,
               'orders': orders, 'spot_orders': spot_orders, 'active': active, 'fees': fees,
               'funding': funding, 'book': book, 'funding_payments': payments, 'fills': fills}
        return {'symbol': p.symbol, 'collateral': p.collateral, 'started_ms': started,
                'received_ms': received, 'book_ms': book['time'], 'mode': mode,
                'collateral_scope_verified': True, 'account_consistent': (
                    [(x['position']['coin'], x['position']['szi'], x['position']['leverage'])
                     for x in before['assetPositions']] ==
                    [(x['position']['coin'], x['position']['szi'], x['position']['leverage'])
                     for x in after['assetPositions']]
                    and {(b['coin'], b['token']) for b in spot['balances']} ==
                    {(b['coin'], b['token']) for b in spot_after['balances']}
                    and (bool(selected) or spot['balances'] == spot_after['balances'])),
                'other_positions': other, 'open_orders': orders + spot_orders,
                'sz_decimals': spec['szDecimals'], 'delisted': spec.get('isDelisted', False),
                'max_leverage': spec['maxLeverage'], 'bid': book['levels'][0][0]['px'],
                'ask': book['levels'][1][0]['px'], 'levels': book['levels'],
                'mark': ctx['markPx'], 'oracle': ctx['oraclePx'], 'fee_bps': str(fee_bps),
                'equity': str(equity), 'free': str(free_value), 'quantity': quantity,
                'leverage': active['leverage'], 'max_trade_sizes': active['maxTradeSzs'],
                'available_to_trade': active['availableToTrade'], 'funding': funding, 'raw': raw}

    def enable_signer(self):
        if not self.policy.enabled:
            raise ValueError('Environment trading disabled')
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        # Read the key only after all non-signing checks and explicit execute gate.
        key = os.environ.get('API_WALLET_PRIVATE_KEY', '')
        if not key and self.env_path.exists():
            for line in self.env_path.read_text(encoding='utf-8-sig').splitlines():
                name, sep, value = line.partition('=')
                if sep and name.strip() == 'API_WALLET_PRIVATE_KEY':
                    key = value.strip().strip('\"\'')
        try:
            signer = Account.from_key(key)
        except Exception:
            raise ValueError('Missing/invalid local API signer') from None
        key = None
        self.signer_address = address(signer.address)
        if (self.signer_address != address(self.settings['API_WALLET_ADDRESS'])
                or self.signer_address == self.team):
            raise ValueError('API signer identity mismatch; team signing forbidden')
        self.check_authorization()
        self.exchange = Exchange(signer, TESTNET_URL, account_address=self.team,
                                 perp_dexs=[self.policy.dex], timeout=15)

    def check_authorization(self):
        agents = self.query('extraAgents', user=self.team)
        if not any(address(a['address']) == self.signer_address
                   and a['validUntil'] > self.clock() + 60_000 for a in agents):
            raise ValueError('API authorization absent/expired')

    def set_leverage(self):
        self.check_authorization()
        response = self.exchange.update_leverage(1, self.policy.symbol, is_cross=True)
        if response.get('status') != 'ok':
            raise ValueError('Leverage setup rejected')
        return response

    def send(self, order):
        from hyperliquid.utils.types import Cloid
        if not self.exchange or order['symbol'] != self.policy.symbol:
            raise ValueError('Signer not enabled or wrong market')
        self.check_authorization()
        self.exchange.set_expires_after(self.clock() + 10_000)
        return self.exchange.order(order['symbol'], order['buy'], float(order['quantity']),
                                   float(order['price']), {'limit': {'tif': 'Ioc'}},
                                   reduce_only=order['reduce_only'], cloid=Cloid.from_str(order['cloid']))

    def order_status(self, cloid):
        return self.query('orderStatus', user=self.team, oid=cloid)

    def cancel(self, cloid):
        from hyperliquid.utils.types import Cloid
        if not self.exchange:
            raise ValueError('Signer not enabled')
        self.check_authorization()
        self.exchange.set_expires_after(self.clock() + 10_000)
        return self.exchange.cancel_by_cloid(self.policy.symbol, Cloid.from_str(cloid))

    def close(self):
        self.session.close()
        if self.exchange:
            self.exchange.session.close()
            self.exchange.info.session.close()
