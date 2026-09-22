"""A seat whose mandate is not directional does not vote.

Measured across 58 completed debates, both asset classes:

    quant             32/54 directional (59%)
    sentiment         24/54 (44%)
    devils_advocate   16/54 (30%)
    risk              13/54 (24%)
    analyst            5/27 (19%)
    corroborator       0/54  (0%)   <-- never, in either asset class

The Corroborator has never once expressed a direction, and it never will: its
mandate is verifying whether the FACTS are sourced, which is orthogonal to
whether the price goes up. It answers "1 field confirmed, 3 unverified", and
the only honest signal for that is neutral.

That answer was being COUNTED. In a five-seat crypto table it was a permanent
fifth of the balance of opinion arguing for nothing, and the chair read the
resulting pile of neutrals as a committee-wide stand-aside.

The earlier eligibility fix deliberately left this alone, on the grounds that
crypto-only evidence could not distinguish "wrong mandate for this asset" from
"wrong seat design". Fifty-four debates across both classes settle it: this is
the seat, not the asset.

So it becomes ADVISORY. It still speaks, the chair still reads it, and its
concerns still reach the transcript — the Corroborator exists because nothing
else attacked the FACTS, and that job is untouched. It simply stops casting a
vote it has never used.

Nothing else changes: this is not a re-weighting, and calibration could not
have achieved it. A weight multiplies a vote; it cannot turn an abstention into
a direction.
"""

import pytest

from roundtable.engine import RoundTable
from roundtable.seats import ALL_SEATS, SEATS_BY_ID, voting_seats
from roundtable.types import SeatOpinion


def _op(seat, signal, failed=False):
    return SeatOpinion(seat_id=seat, seat_name=seat.title(), signal=signal,
                       confidence=55.0, reasoning="r", failed=failed)


def test_the_corroborator_does_not_vote():
    assert SEATS_BY_ID["corroborator"].votes is False


def test_every_other_seat_does():
    for seat in ALL_SEATS:
        if seat.id != "corroborator":
            assert seat.votes is True, seat.id


def test_it_still_sits_and_still_speaks():
    """Advisory is not removal. It exists because nothing else attacked the
    facts, and that job is unchanged."""
    from roundtable.seats import eligible_seats
    assert "corroborator" in [s.id for s in eligible_seats("crypto")]
    assert "corroborator" in [s.id for s in eligible_seats("equity")]


def test_voting_seats_excludes_it():
    ids = [s.id for s in voting_seats("crypto")]
    assert "corroborator" not in ids
    assert "quant" in ids and "risk" in ids


# ---------------------------------------------------------------- the tally

def _block(opinions):
    return RoundTable(client=None)._tally_block(opinions)


def test_its_neutral_is_not_counted_as_a_no_view_vote():
    """The defect. One bullish against four neutrals read as a stand-aside;
    one of those four was a seat that cannot hold a view at all."""
    block = _block([_op("quant", "bullish"), _op("risk", "neutral"),
                    _op("corroborator", "neutral")])
    assert "1 bullish / 0 bearish / 1 no view" in block


def test_the_table_size_shrinks_honestly():
    """Four voting seats on crypto, not five. A denominator that counts a seat
    which cannot vote overstates how thin the support is."""
    block = _block([_op("quant", "bullish"), _op("risk", "neutral"),
                    _op("sentiment", "neutral"), _op("devils_advocate", "neutral"),
                    _op("corroborator", "neutral")])
    assert "1 bullish / 0 bearish / 3 no view" in block


def test_its_verification_still_reaches_the_chair():
    """The whole point of keeping it. Dropping it from the render would lose
    the one seat that checks whether the numbers were sourced at all."""
    rendered = RoundTable(client=None)._render_opinions([
        _op("quant", "bullish"), _op("corroborator", "neutral")])
    assert "Corroborator" in rendered


def test_a_directional_answer_from_it_would_still_not_vote():
    """Defensive. If the model ever returns bullish from a verification seat,
    that is a prompt failure and must not become a vote."""
    block = _block([_op("quant", "bullish"), _op("corroborator", "bullish")])
    assert "1 bullish" in block


def test_an_all_advisory_table_produces_no_tally_rather_than_a_verdict():
    block = _block([_op("corroborator", "neutral")])
    assert "0 bullish / 0 bearish / 0 no view" in block or block == ""
