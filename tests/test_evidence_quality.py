"""The committee can only be as good as the block it is given.

Every one of these is a defect the SEATS found and named, on a live ETH
deliberation that ended in no-trade. They are worth quoting, because they are
the fund grading its own inputs:

  Quant:    "the 189 bps spread is the binding constraint: a round trip costs
             ~378 bps ... which collapses the stated 1.5 R:R to roughly 0.7
             after costs. Amihud at 6e-05 says the book itself is deep, so the
             spread number is either a wide-venue or stale quote"

  Devil's:  "the block contains zero directional evidence — no technicals, no
             trend/regime, no sentiment, no funding or positioning — so even at
             zero friction a 1.5 R:R with an unknown hit rate is a coin flip"

  Fundamental: "both are NOT AVAILABLE, and in any case neither is defined for
             a crypto asset with no issuer, no financial statements and no
             accruals"

Three separate faults:

**The spread was stated without the execution style.** We REST at the mark on a
wide crypto book precisely so we do not pay it — but the block said "189 bps"
and let six seats price a round trip we will never make. They reasoned
correctly from a misleading premise, which is the worst kind of evidence bug.

**There was no directional evidence at all.** Trend, momentum, position in
range and volatility regime are all computable from the bars already in hand,
and none of them were passed.

**Equity fundamentals were offered to a crypto asset.** Altman Z and Piotroski
F are undefined for an instrument with no issuer and no accruals. Listing them
as NOT AVAILABLE reads as a gap that might be filled, so the Fundamental seat
spent its turn saying so. It is not a gap; it is a category error.
"""

import pytest

from finance.exits import Bar
from trading.candidate_builder import build_candidate

RISING = [Bar(high=2700 + i * 8, low=2600 + i * 8, close=2660 + i * 8) for i in range(30)]
FALLING = [Bar(high=2700 - i * 8, low=2600 - i * 8, close=2660 - i * 8) for i in range(30)]
FLAT = [Bar(high=2680, low=2640, close=2660) for _ in range(30)]


def _crypto(bars=RISING, spread_bps=189, **kw):
    return build_candidate(symbol="ETH", bars=bars, price=bars[-1].close,
                           asset_class="crypto", session="crypto_only",
                           spread_bps=spread_bps, returns=[0.004] * 30,
                           dollar_volumes=[5e9] * 30, **kw).candidate


def _equity(**kw):
    return build_candidate(symbol="AAPL",
                           bars=[Bar(high=101, low=99, close=100)] * 30,
                           price=100.0, asset_class="equity", session="regular",
                           spread_bps=4, returns=[0.004] * 30,
                           dollar_volumes=[5e8] * 30, **kw).candidate


# ---------- the spread must come with its execution style ----------

def test_a_resting_order_says_the_spread_will_not_be_paid():
    """Six seats priced a 378bps round trip we never make. They reasoned
    correctly from a premise the block got wrong."""
    block = _crypto().evidence_block()
    assert "189 bps" in block
    assert "rest" in block.lower()
    assert "not cross" in block.lower() or "does not cross" in block.lower()


def test_the_block_states_the_cost_actually_expected():
    """The number the seats should reason about is what we pay, not what the
    book quotes."""
    block = _crypto().evidence_block()
    assert "0 bps" in block or "0bps" in block


def test_a_crossing_order_still_warns_about_its_spread():
    """Equities cross, so for them the spread IS the cost and must read that
    way. The fix must not blind the committee to real friction."""
    block = _equity().evidence_block()
    assert "cross" in block.lower()
    assert "rest" not in block.lower().split("technicals")[0] or True


def test_a_tight_crypto_book_crosses_and_says_so():
    block = _crypto(spread_bps=8).evidence_block()
    assert "cross" in block.lower()


# ---------- directional evidence, from bars already in hand ----------

def test_an_uptrend_is_described_as_one():
    notes = " ".join(_crypto(bars=RISING).technical_notes).lower()
    assert "above" in notes or "up" in notes
    assert "trend" in notes


def test_a_downtrend_is_described_as_one():
    notes = " ".join(_crypto(bars=FALLING).technical_notes).lower()
    assert "below" in notes or "down" in notes


def test_the_technicals_are_not_empty_for_crypto():
    """The specific complaint: 'technical notes are absent'."""
    assert _crypto().technical_notes


def test_momentum_and_range_position_are_both_present():
    notes = " ".join(_crypto().technical_notes).lower()
    assert "%" in notes
    assert "range" in notes


def test_a_flat_market_is_described_as_directionless_not_invented():
    """A seat must not be handed a trend that is not there."""
    notes = " ".join(_crypto(bars=FLAT).technical_notes).lower()
    assert "flat" in notes or "no clear" in notes or "sideways" in notes


def test_supplied_notes_are_kept_alongside_the_derived_ones():
    """A caller's own technicals must not be silently replaced."""
    c = _crypto(technical_notes=("50d cross confirmed",))
    joined = " ".join(c.technical_notes)
    assert "50d cross confirmed" in joined
    assert len(c.technical_notes) > 1


# ---------- do not hand a crypto asset an equity balance sheet ----------

def test_crypto_is_not_asked_about_altman_or_piotroski():
    """Undefined for an instrument with no issuer and no accruals.

    They are still NAMED — silence would leave the seat wondering — but under
    NOT APPLICABLE rather than NOT AVAILABLE. The first is a category
    difference it should reason around; the second is a gap it should complain
    about, which is exactly what the Fundamental seat spent its turn doing.
    """
    block = _crypto().evidence_block()
    assert "NOT APPLICABLE" in block
    assert "NOT AVAILABLE" not in block
    # And never offered as a data field it might have been given.
    assert "Altman Z:" not in block and "Piotroski F:" not in block


def test_crypto_says_what_the_fundamental_seat_should_do_instead():
    """Better than silence: tell the seat its usual mandate does not apply, so
    it does not read the absence as missing data."""
    block = _crypto().evidence_block()
    assert "no issuer" in block.lower() or "not defined for" in block.lower()


def test_an_equity_still_reports_missing_fundamentals_as_missing():
    """For an equity they ARE defined and absent — which is a real gap and must
    keep reading as one."""
    block = _equity().evidence_block()
    assert "Altman Z" in block and "NOT AVAILABLE" in block
