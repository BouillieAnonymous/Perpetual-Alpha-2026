"""Explicit testnet venue identity. Never alias rehearsal data to competition."""
from dataclasses import dataclass

ASSETS = ('BTC', 'ETH', 'HYPE', 'SP500', 'EWY', 'EWJ', 'BRENT', 'GOLD', 'USDEUR', 'USDJPY')


@dataclass(frozen=True)
class Venue:
    dex: str
    collateral: str

    @property
    def source(self):
        return 'hyperliquid-testnet/'+self.dex

    @property
    def symbols(self):
        return tuple(self.dex+':'+asset for asset in ASSETS)

    def validate_symbol(self, symbol):
        if symbol not in self.symbols:
            raise ValueError('Symbol does not belong to selected venue')
        return symbol


def venue(dex='par'):
    if dex not in {'par', 'pas'}:
        raise ValueError('Explicit par rehearsal or pas competition venue required')
    return Venue(dex, 'USDPR' if dex == 'par' else 'USDP')


def source_venue(source):
    for dex in ('par', 'pas'):
        selected = venue(dex)
        if source == selected.source:
            return selected
    raise ValueError('Unknown market source')


def symbol_venue(symbol):
    if not isinstance(symbol, str) or ':' not in symbol:
        raise ValueError('Explicit venue-prefixed symbol required')
    selected = venue(symbol.split(':', 1)[0])
    selected.validate_symbol(symbol)
    return selected


def common_venue(symbols):
    selected = {symbol_venue(s) for s in symbols}
    if len(selected) != 1:
        raise ValueError('One nonempty unmixed venue required')
    return selected.pop()


