"""One watchlist symbol, two vendors, two incompatible spellings.

Verified live against both providers before the fix:

    spelling    Massive bars            Robinhood quote
    BTC         X:BTCUSD        ok      get_crypto_quotes(["BTC"])      0 rows
    BTC-USD     BTC-USD         wrong   get_crypto_quotes(["BTC-USD"])  1 row

So under `BTC` the fund got history and every paper fill was rejected "no quote
available"; under `BTC-USD` it got a quote and `get_history` returned None, so
the candidate was pre-screened out as "no price data available" — which is what
a real cycle reported. **Crypto could not trade under either spelling.**

Neither format is "right": each vendor owns its own, so the normalisation
belongs at each boundary rather than in the watchlist. A fund whose config has
to know a vendor's ticker convention will get it wrong again the next time a
provider is added.
"""

import pytest

from trading.massive_provider import _ticker
from trading.venues.robinhood import _crypto_pair, _is_crypto


# ---------- what counts as crypto ----------

@pytest.mark.parametrize("symbol", ["BTC", "ETH", "SOL", "btc", "BTC-USD", "eth-usd"])
def test_crypto_is_recognised_in_either_spelling(symbol):
    assert _is_crypto(symbol)


@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "F", "BRK.B"])
def test_equities_are_not_mistaken_for_crypto(symbol):
    assert not _is_crypto(symbol)


# ---------- Robinhood wants the hyphenated pair ----------

@pytest.mark.parametrize("symbol, expected", [
    ("BTC", "BTC-USD"),
    ("btc", "BTC-USD"),
    ("BTC-USD", "BTC-USD"),
    ("ETH", "ETH-USD"),
    ("eth-usd", "ETH-USD"),
])
def test_the_venue_is_always_given_a_hyphenated_pair(symbol, expected):
    """`get_crypto_quotes(["BTC"])` returns zero rows — verified live."""
    assert _crypto_pair(symbol) == expected


# ---------- Massive wants X:<SYM>USD ----------

@pytest.mark.parametrize("symbol, expected", [
    ("BTC", "X:BTCUSD"),
    ("BTC-USD", "X:BTCUSD"),
    ("btc-usd", "X:BTCUSD"),
    ("ETH-USD", "X:ETHUSD"),
    ("X:BTCUSD", "X:BTCUSD"),
])
def test_massive_maps_both_spellings_to_its_own_ticker(symbol, expected):
    """`BTC-USD` used to pass straight through as a bogus equity ticker, which
    is why history came back None and every candidate was pre-screened out."""
    assert _ticker(symbol) == expected


@pytest.mark.parametrize("symbol", ["AAPL", "MSFT"])
def test_equity_tickers_pass_through_massive_unchanged(symbol):
    assert _ticker(symbol) == symbol


# ---------- the two must agree on the same watchlist entry ----------

@pytest.mark.parametrize("watchlist_entry", ["BTC", "BTC-USD", "ETH", "ETH-USD"])
def test_either_spelling_reaches_both_vendors_correctly(watchlist_entry):
    """The actual requirement. Whatever the operator writes in
    FUND_CRYPTO_WATCHLIST, bars AND quotes must both resolve."""
    base = watchlist_entry.split("-")[0].upper()
    assert _ticker(watchlist_entry) == f"X:{base}USD"
    assert _crypto_pair(watchlist_entry) == f"{base}-USD"
