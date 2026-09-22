"""Any BASE-USD pair is crypto, not just the handful someone listed.

Caught live: four of ten screened names — ZEC, SUI, NEAR, PEPE — were
pre-screened out with "no price data available" while Robinhood quoted all of
them fine. Massive had the bars; we were asking for them under the wrong ticker.

`_ticker` mapped a pair to `X:BASEUSD` only when BASE appeared in a hardcoded
`_CRYPTO_BASES` list, so anything outside it fell through and was sent as an
EQUITY ticker. `ZEC-USD` went out as `ZEC-USD` and returned zero rows.

The docstring on that function already describes this exact failure being fixed
once, for BTC-USD. It was fixed by adding to the list rather than by removing
the need for one — so the bug came straight back the moment the crypto scout
grew the universe from two hand-typed names to fifty-eight screened ones.

The general rule already exists elsewhere in this codebase:
`trading.fund.classify_asset_class` treats any `BASE-USD` as crypto, because
Robinhood spells every pair that way and no US equity ticker contains a hyphen.
Using the same rule here means a list cannot go stale again.
"""

import pytest

from trading.massive_provider import _ticker


@pytest.mark.parametrize("pair,expected", [
    ("BTC-USD", "X:BTCUSD"),
    ("ZEC-USD", "X:ZECUSD"),     # the ones that were silently dropped
    ("SUI-USD", "X:SUIUSD"),
    ("NEAR-USD", "X:NEARUSD"),
    ("PEPE-USD", "X:PEPEUSD"),
    ("MORPHO-USD", "X:MORPHOUSD"),
    ("zec-usd", "X:ZECUSD"),
])
def test_every_pair_maps_to_a_crypto_ticker(pair, expected):
    assert _ticker(pair) == expected


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "NVDA", "F", "BRK.B"])
def test_an_equity_ticker_passes_through(ticker):
    assert _ticker(ticker) == ticker


def test_an_already_prefixed_ticker_is_left_alone():
    assert _ticker("X:BTCUSD") == "X:BTCUSD"


def test_a_bare_crypto_symbol_still_maps():
    """The override path uses bare symbols (FUND_CRYPTO_WATCHLIST=BTC,ETH),
    which carry no suffix — so the known-base list still earns its keep for
    those, it just stopped being the ONLY rule."""
    assert _ticker("BTC") == "X:BTCUSD"
    assert _ticker("ETH") == "X:ETHUSD"


def test_the_mapping_agrees_with_the_fund_classifier():
    """One instrument, one answer. These two disagreeing is how a name the
    scout screened became a name the provider could not price."""
    from trading.fund import classify_asset_class
    for pair in ("ZEC-USD", "SUI-USD", "AVAX-USD", "PEPE-USD"):
        assert classify_asset_class(pair) == "crypto"
        assert _ticker(pair).startswith("X:")
