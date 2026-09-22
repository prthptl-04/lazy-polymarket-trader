"""Seat influence reaches the chair as arithmetic, not only as prose.

This closes a gap that has been open since seat weighting was built.
`roundtable.calibration.seat_weights` grades every seat on its Brier score and
produces a multiplier between 0.40 and 1.50 — and `Thesis.weighted_tally`
applied it in exactly one place: `RoundTable._fallback_consensus`, the path that
runs only when the chair LLM has already failed.

On the normal path the weight reached the chair as a sentence beside each
opinion ("this seat's calls have been better than average; weight 1.23") and the
model was left to do the arithmetic in prose. The balance-of-opinion block added
last commit made it worse in one narrow way: it gave the chair a clean COUNT,
and a raw count silently treats a discredited seat and a proven one as one vote
each.

Both numbers are now shown, because they answer different questions:

  raw       who said what — the transcript, and it must not re-weight itself
  weighted  what the record says that adds up to — the decision-relevant number

Suppressed entirely while every seat sits at 1.00x, which is the state today
with nothing resolved. A "weighted" line identical to the raw one is noise in a
prompt that is paid for by the token, and it would imply an adaptation that has
not happened yet.
"""

import pytest

from roundtable.engine import RoundTable
from roundtable.types import SeatOpinion


def _op(seat, signal):
    return SeatOpinion(seat_id=seat, seat_name=seat.title(), signal=signal,
                       confidence=55.0, reasoning="r")


def _block(opinions, weights=None):
    return RoundTable(client=None, seat_weights=weights or {})._tally_block(opinions)


UNSCORED = [_op("quant", "bullish"), _op("risk", "neutral"), _op("sentiment", "neutral")]


def test_unweighted_seats_produce_no_weighted_line():
    """Today's state: nothing resolved, every seat at 1.00x. A weighted line
    identical to the raw one implies an adaptation that has not happened."""
    block = _block(UNSCORED)
    assert "weighted" not in block.lower()


def test_weights_that_are_all_one_are_still_suppressed():
    block = _block(UNSCORED, {"quant": 1.0, "risk": 1.0, "sentiment": 1.0})
    assert "weighted" not in block.lower()


def test_a_divergent_record_produces_a_weighted_line():
    block = _block(UNSCORED, {"quant": 1.4, "risk": 0.6, "sentiment": 0.5})
    assert "weighted" in block.lower()


def test_the_weighted_count_reflects_the_multipliers():
    """Two half-weight bears do not outvote one full-weight bull."""
    ops = [_op("quant", "bullish"), _op("risk", "bearish"), _op("sentiment", "bearish")]
    block = _block(ops, {"quant": 1.5, "risk": 0.4, "sentiment": 0.4})
    assert "1.50 bullish" in block and "0.80 bearish" in block


def test_the_raw_count_is_never_re_weighted():
    """The transcript stays a transcript. A record that silently re-weights
    itself is not one, and the chair must be able to see both."""
    ops = [_op("quant", "bullish"), _op("risk", "bearish")]
    block = _block(ops, {"quant": 1.5, "risk": 0.4})
    assert "1 bullish / 1 bearish" in block


def test_a_down_weighted_seat_is_named_with_its_multiplier():
    """A number with no attribution cannot be argued with. The chair should be
    able to disagree with the weighting, which means seeing it."""
    block = _block(UNSCORED, {"quant": 1.4, "risk": 0.5, "sentiment": 1.0})
    assert "Risk" in block and "0.5" in block


def test_an_unknown_seat_counts_as_full_weight():
    """An unscored seat is untested, not discredited — the same stance
    `seat_weights` takes."""
    ops = [_op("quant", "bullish"), _op("newcomer", "bullish")]
    block = _block(ops, {"quant": 1.5})
    assert "2.50 bullish" in block


def test_the_weighted_line_never_recommends_a_verdict():
    block = _block(UNSCORED, {"quant": 1.4, "risk": 0.5})
    for banned in ("you should", "therefore", "recommend", "execute"):
        assert banned not in block.lower(), banned


def test_the_weighting_remains_measurable():
    """The whole justification for arithmetic weighting over prompt rewriting is
    that it is falsifiable. `roundtable.replay` re-decides stored theses under a
    weighting and reports whether it beat the chair — that path must stay."""
    from roundtable.replay import weighted_arm
    assert callable(weighted_arm)
