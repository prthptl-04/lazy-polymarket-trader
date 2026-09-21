"""Spread and Amihud measure different things, and the block must say so.

Across four live deliberations the Devil's Advocate raised, and the chair
adopted, this objection:

    The entire no-trade case is load-bearing on one single-sourced figure: the
    186 bps spread. It is contradicted by Amihud 1.9e-05, which says the book
    is deep.

Both figures are correct and they do not contradict each other. Verified
against the live Robinhood quote on 2026-09-21: BTC bid 80,902.88 / ask
82,409.59 / mark 81,656.23 is a real 184.5 bps quoted spread, and ETH is 186.4.

They measure different quantities on different venues:

- **Spread** is what this broker charges for immediacy right now — a retail
  markup on a market-maker-routed quote.
- **Amihud** is price impact per dollar traded, computed from bar returns and
  volumes in the underlying market. It is a measure of DEPTH.

A deep underlying market carrying a wide retail quote is exactly what Robinhood
crypto is. Listing the two adjacently with no distinction invited a comparison
that is not meaningful, and it cost the fund four stand-asides.

This is the cheapest class of fix available: no new data, no new call, no model
change — the committee was reasoning correctly from a block that misled it.
"""

from roundtable.types import Candidate


def _block(**kw):
    return Candidate(symbol="BTC", asset_class="crypto", price=81656.0, **kw).evidence_block()


def test_the_spread_says_what_it_is_the_cost_of():
    block = _block(spread_bps=185)
    assert "185" in block
    assert "immediacy" in block.lower() or "demanding" in block.lower()


def test_amihud_is_labelled_as_depth_not_as_spread():
    block = _block(amihud_illiquidity=1.9e-05)
    assert "depth" in block.lower()


def test_the_block_states_that_the_two_do_not_contradict():
    """The specific false objection, pre-empted. Four debates were lost to it."""
    block = _block(spread_bps=185, amihud_illiquidity=1.9e-05)
    assert "contradict" in block.lower()


def test_neither_label_appears_without_its_number():
    """A note about a figure that is absent is noise in a paid prompt, and
    invites reasoning about a measurement that was never taken."""
    assert "immediacy" not in _block(amihud_illiquidity=1.9e-05).lower()
    assert "depth" not in _block(spread_bps=185).lower()


def test_a_candidate_with_neither_says_nothing_about_liquidity():
    block = _block()
    assert "contradict" not in block.lower()
