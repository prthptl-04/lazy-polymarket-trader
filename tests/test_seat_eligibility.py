"""A seat with no mandate for an asset class must not vote.

The defect this fixes, measured over 12 live crypto deliberations: the
committee returned neutral 12 times out of 12 and submitted nothing. Four of
seven seats had never once expressed a direction, because on an instrument with
no issuer they have nothing to reason from — and a seat with nothing to reason
from correctly answers "neutral".

The trap is that a neutral answer is COUNTED. Only an errored seat abstains and
is excluded from the tally. So the chair read "six of seven seats are neutral"
as a committee-wide stand-aside, when four of them were never eligible to speak.

Which seats are excluded is decided on mandate, not on observed behaviour:

- **Fundamental Analyst** — Altman Z and Piotroski F are undefined for an asset
  with no financial statements. Structurally ineligible.
- **Catalyst Analyst** — no earnings date, no 8-K, no Form 4 exists. Its
  headlines belong to the Sentiment seat's mandate, not its own.

The Corroborator is NOT excluded, though it also voted neutral 10 times out of
10. Its mandate is verification, which is non-directional on every asset class
including equities — that is a question about whether it should vote at all,
and answering it here on crypto evidence alone would be fitting the fix to the
symptom.
"""

import pytest

from roundtable.seats import ALL_SEATS, eligible_seats
from roundtable.types import Candidate, SeatOpinion, Thesis


def _ids(asset_class):
    return [s.id for s in eligible_seats(asset_class)]


def test_every_seat_is_eligible_on_equities():
    """The committee was designed for equities. Nothing is muted there."""
    assert len(_ids("equity")) == len(ALL_SEATS)


def test_the_two_seats_with_no_crypto_mandate_are_excluded():
    ids = _ids("crypto")
    assert "analyst" not in ids, "no financial statements exist for crypto"
    assert "catalyst" not in ids, "no earnings, filings or Form 4 exists for crypto"


def test_the_directional_seats_survive_on_crypto():
    """Muting a seat that CAN reason is how a fix becomes a bias."""
    ids = _ids("crypto")
    for seat in ("quant", "risk", "sentiment", "devils_advocate"):
        assert seat in ids, seat


def test_the_corroborator_still_votes_on_crypto():
    """It voted neutral in 10 of 10 crypto debates, but its mandate is
    verification — non-directional on equities too. Excluding it on crypto
    evidence would be fitting the fix to the symptom rather than the cause."""
    assert "corroborator" in _ids("crypto")


def test_an_unknown_asset_class_keeps_the_whole_table():
    """Refuse to mute on a guess. Silencing a seat is the dangerous direction:
    it removes a view, and a view removed is never recorded as missing."""
    assert len(_ids("commodity")) == len(ALL_SEATS)


def test_the_round_two_seat_is_never_muted():
    """The Devil's Advocate's mandate is dissent, which applies to any asset."""
    assert "devils_advocate" in _ids("crypto")


# ---------------------------------------------------------------- the tally

def _thesis(signals, asset_class="crypto"):
    t = Thesis(thesis_id="t", symbol="ETH", asset_class=asset_class)
    t.opinions = [
        SeatOpinion(seat_id=sid, seat_name=sid, signal=sig, confidence=55.0,
                    reasoning="")
        for sid, sig in signals
    ]
    return t


def test_a_lone_directional_voice_is_a_majority_once_the_mute_seats_are_gone():
    """The arithmetic the fix exists for. Before: 1 bullish against 6 neutrals
    read as a stand-aside. After: 1 bullish among 5 eligible, with nothing
    bearish against it."""
    t = _thesis([("quant", "bullish"), ("risk", "neutral"),
                 ("sentiment", "neutral"), ("corroborator", "neutral"),
                 ("devils_advocate", "neutral")])
    tally = t.weighted_tally({})
    assert tally["bullish"] == 1 and tally["bearish"] == 0


def test_an_ineligible_seat_never_reaches_the_tally_at_all():
    """Not down-weighted — absent. A muted seat that still contributed a zero
    would leave the chair counting it."""
    t = _thesis([("quant", "bullish"), ("risk", "neutral")])
    assert sum(t.weighted_tally({}).values()) == 2


# ---------------------------------------------------------------- the chair

def test_the_chair_is_told_how_many_seats_were_eligible():
    """It was counting ineligible neutrals as stand-aside opinions. "1 of 3
    eligible" and "1 of 7 seats" describe the same vote and imply opposite
    conclusions."""
    from roundtable.engine import RoundTable
    table = RoundTable(client=None)
    rendered = table._eligibility_note("crypto")
    assert "5 of 7" in rendered
    assert "Fundamental Analyst" in rendered and "Catalyst Analyst" in rendered


def test_the_note_is_silent_when_every_seat_is_eligible():
    """On equities there is nothing to explain, and a line saying "7 of 7" is
    noise in a cache-tagged prompt."""
    from roundtable.engine import RoundTable
    assert RoundTable(client=None)._eligibility_note("equity") == ""


def test_the_candidate_block_says_which_seats_are_absent():
    """The seats that DO sit must know the table is thinner than usual, or a
    sparse vote reads as weak conviction rather than a smaller committee."""
    block = Candidate(symbol="ETH", asset_class="crypto", price=100.0).evidence_block()
    assert "NOT CONSULTED" in block
    assert "Fundamental Analyst" in block
