"""The fund screens the whole tradable crypto market, not two hand-typed names.

Why this exists. `FUND_CRYPTO_WATCHLIST` held BTC and ETH, so every weekend
cycle put the same two instruments to a committee that costs eight model calls
each. Over 21 deliberations it declined all of them — correctly, on the evidence
it had. Two names is not a market, it is a standing bet that the opportunity is
in the two you typed, and `MarketScout` already refuses that bet for equities:

    A hand-typed list of tickers is a standing bet that the opportunity is
    where you last looked, and it goes stale silently.

Crypto had no equivalent, and `_universe_for` said so in an error message.

Two sources, because neither alone is enough:

- **Robinhood's pair list** is what we can actually TRADE. 58 pairs are
  tradable today out of 91 listed; screening a name the broker will not sell us
  is a deliberation spent on nothing.
- **Massive's grouped crypto aggregate** is one call for ~392 tickers with
  volume, OHLC and trade count — the same shape the equity scout screens.

The intersection is the universe. Ranking is the equity scout's, unchanged:
volume outweighs the move, because a big move on thin volume is usually one
motivated buyer and a spread you cannot get out through.
"""

import pytest

from trading.crypto_discovery import CryptoScout


def _row(sym, close, open_, vol, n=1000):
    return {"T": sym, "c": close, "o": open_, "v": vol, "n": n}


def _scout(rows, pairs=("BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD"), **kw):
    return CryptoScout(grouped=lambda day: rows, pairs=lambda: list(pairs), **kw)


def test_only_pairs_the_broker_will_actually_trade_are_screened():
    """A deliberation on a name we cannot buy is eight model calls spent on
    nothing."""
    rows = [_row("X:BTCUSD", 80000, 79000, 5000),
            _row("X:FAKEUSD", 10, 9, 5_000_000)]
    out = _scout(rows).scan()
    assert [c.symbol for c in out] == ["BTC-USD"]


def test_the_massive_ticker_spelling_maps_to_the_robinhood_one():
    """Massive says X:BTCUSD, Robinhood says BTC-USD, the fund says BTC. One of
    these mismatches is how a universe silently comes back empty."""
    out = _scout([_row("X:SOLUSD", 200, 190, 100_000)]).scan()
    assert out and out[0].symbol == "SOL-USD"


def test_a_stablecoin_is_not_a_trade():
    """USDC against USD cannot move enough to pay for a round trip, and its
    tiny moves would rank it on volume alone."""
    rows = [_row("X:USDCUSD", 1.0, 1.0, 900_000_000)]
    assert _scout(rows, pairs=("USDC-USD",)).scan() == []


def test_volume_outranks_the_bigger_move():
    """The equity scout's rule, kept: a large move on thin volume is usually
    one motivated buyer."""
    rows = [_row("X:DOGEUSD", 0.4, 0.3, 1_000),        # +33% on nothing
            _row("X:BTCUSD", 80000, 78000, 100_000)]   # +2.6% on real volume
    assert [c.symbol for c in _scout(rows).scan()][0] == "BTC-USD"


def test_a_name_that_has_already_run_is_not_chased():
    rows = [_row("X:DOGEUSD", 1.0, 0.4, 10_000_000)]   # +150%
    assert _scout(rows, pairs=("DOGE-USD",)).scan() == []


def test_a_flat_tape_is_not_a_setup():
    rows = [_row("X:BTCUSD", 80000, 79999.9, 10_000_000)]
    assert _scout(rows).scan() == []


def test_thin_dollar_volume_is_refused_as_an_exit_test():
    """Not a popularity filter. A position you cannot leave is one you did not
    really size."""
    rows = [_row("X:SOLUSD", 200, 190, 1)]
    assert _scout(rows).scan() == []


def test_the_scan_is_capped_so_a_cycle_cannot_run_away():
    rows = [_row(f"X:C{i}USD", 100 + i, 90 + i, 10_000_000) for i in range(30)]
    pairs = [f"C{i}-USD" for i in range(30)]
    assert len(_scout(rows, pairs=pairs).scan(limit=5)) == 5


def test_a_dead_pair_list_yields_nothing_rather_than_everything():
    """Failing open here would screen 392 tickers against a broker that trades
    58 of them."""
    s = CryptoScout(grouped=lambda day: [_row("X:BTCUSD", 80000, 79000, 5000)],
                    pairs=lambda: (_ for _ in ()).throw(RuntimeError("mcp down")))
    assert s.scan() == []


def test_a_dead_grouped_call_yields_nothing_rather_than_raising():
    s = CryptoScout(grouped=lambda day: (_ for _ in ()).throw(RuntimeError("no data")),
                    pairs=lambda: ["BTC-USD"])
    assert s.scan() == []


def test_every_candidate_explains_itself():
    """The cycle report shows these. "BTC-USD" alone does not say why it is
    there, and an unexplained candidate is one nobody can audit later."""
    out = _scout([_row("X:BTCUSD", 80000, 78000, 100_000)]).scan()
    assert "%" in out[0].reason and "M traded" in out[0].reason
