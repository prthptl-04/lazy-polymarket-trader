"""What kind of instrument is this? Decided by the symbol, not by a config list.

The bug, introduced by the crypto scout and caught in production by the
operator: asset class was `"crypto" if symbol in self.crypto_watchlist else
"equity"`. Emptying FUND_CRYPTO_WATCHLIST is what LETS the scout screen the
whole market — and it simultaneously made every pair the scout found classify
as an equity.

Consequences, all of which were live:

- SEC EDGAR was asked for NEAR-USD's balance sheet
- Altman Z and Piotroski F were attempted on a token
- the earnings calendar, 8-K index and Form 4 feed were queried for a coin
- the two equity-only seats were convened for instruments they have no mandate
  over, which is exactly the deadlock the eligibility fix removed

Classification has to come from the instrument. Robinhood spells every crypto
pair `BASE-USD` and no US equity ticker contains a hyphen, so the symbol itself
carries the answer and no configuration can desynchronise from it.
"""

import pytest

from trading.fund import classify_asset_class


@pytest.mark.parametrize("symbol", [
    "BTC-USD", "ETH-USD", "NEAR-USD", "AVAX-USD", "ZEC-USD", "PEPE-USD",
    "btc-usd", "  SOL-USD  ",
])
def test_a_robinhood_pair_is_crypto(symbol):
    assert classify_asset_class(symbol) == "crypto"


@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "NVDA", "COST", "BRK.B", "F"])
def test_an_equity_ticker_is_equity(symbol):
    assert classify_asset_class(symbol) == "equity"


def test_a_bare_crypto_symbol_is_still_crypto_when_the_watchlist_says_so():
    """The override path. `FUND_CRYPTO_WATCHLIST=BTC,ETH` uses bare symbols,
    and those carry no suffix to classify on — so the configured list still
    counts, it is just no longer the ONLY thing that counts."""
    assert classify_asset_class("BTC", crypto_watchlist=("BTC", "ETH")) == "crypto"
    assert classify_asset_class("AAPL", crypto_watchlist=("BTC", "ETH")) == "equity"


def test_an_empty_watchlist_no_longer_makes_everything_an_equity():
    """The regression, stated directly."""
    assert classify_asset_class("NEAR-USD", crypto_watchlist=()) == "crypto"


def test_a_stablecoin_pair_is_still_crypto():
    """It should never be screened, but if one arrives it is not an equity."""
    assert classify_asset_class("USDC-USD") == "crypto"


def test_the_fund_classifies_a_scout_symbol_correctly(monkeypatch):
    """End to end: the path that was broken."""
    from trading.fund import FundLoop
    fund = FundLoop.__new__(FundLoop)
    fund.crypto_watchlist = ()
    assert fund._asset_class_of("AVAX-USD") == "crypto"
    assert fund._asset_class_of("AAPL") == "equity"
