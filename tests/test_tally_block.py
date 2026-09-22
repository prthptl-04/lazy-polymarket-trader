"""The chair is shown the arithmetic, not only the prose.

Two recommendations from the Gemini architecture review converge on the same
change, and our own data says it is the right one.

**Tyranny of the majority.** The review reports that in divergent multi-agent
debates the minority holds the correct answer in roughly 25.5% of cases, and
that majority voting systematically suppresses it. Our version is worse than
the one it describes: 47 of 51 completed debates ended neutral, and in the
typical one the Quant was bullish, NOBODY was bearish, and the rest had no view.
That is not a committee that disagrees. It is one directional voice and four
abstentions, and the chair read it as a stand-aside.

**Structured payloads over prose.** The review argues that raw conversational
text between agents degrades over long horizons and that typed payloads carry
the same information at higher token efficiency. The chair was reading five
paragraphs and forming an impression of the balance of opinion. Now it is
handed the count.

The distinction this draws is the one the chair could not make by reading:
opposition and absence of a view are different facts. `2 bullish / 0 bearish /
3 no view` and `2 bullish / 3 bearish` are the same headcount and opposite
situations.

Deliberately NOT a meta-classifier that overturns the majority, which is what
the review proposes. That would be an unfalsifiable override sitting on top of
the one part of this system that is measured. The chair still decides; it just
decides with the arithmetic in front of it.
"""

import pytest

from roundtable.engine import RoundTable
from roundtable.types import SeatOpinion


def _op(seat, signal, conf=55.0, failed=False):
    return SeatOpinion(seat_id=seat, seat_name=seat.title(), signal=signal,
                       confidence=conf, reasoning="r", failed=failed)


def _tally(opinions):
    return RoundTable(client=None)._tally_block(opinions)


def test_the_counts_are_stated():
    block = _tally([_op("quant", "bullish"), _op("risk", "bearish"),
                    _op("sentiment", "neutral")])
    assert "1 bullish" in block and "1 bearish" in block and "1 no view" in block


def test_an_unopposed_call_is_named_as_unopposed():
    """The fact the chair kept missing. Nobody argued the other side."""
    block = _tally([_op("quant", "bullish"), _op("risk", "neutral"),
                    _op("sentiment", "neutral"), _op("corroborator", "neutral")])
    assert "no seat argued the other side" in block.lower()


def test_a_genuinely_contested_debate_is_not_called_unopposed():
    block = _tally([_op("quant", "bullish"), _op("risk", "bearish")])
    assert "no seat argued the other side" not in block.lower()


def test_neutral_is_described_as_no_view_not_as_disagreement():
    """A seat with no view has not voted against anything, and the wording is
    what the chair was getting wrong."""
    block = _tally([_op("quant", "bullish"), _op("risk", "neutral")])
    assert "no view" in block.lower()
    assert "against" not in block.split("no seat argued")[0].lower()


def test_an_abstention_is_counted_apart_from_a_neutral():
    """A crashed seat did not weigh in. Folding it into 'no view' would make
    the table look larger than it was."""
    block = _tally([_op("quant", "bullish"), _op("risk", "neutral", failed=True)])
    assert "1 abstained" in block or "abstention" in block.lower()


def test_a_unanimous_table_is_flagged_as_a_caution_not_a_confirmation():
    """The fund's existing stance, carried into the arithmetic: unanimity in an
    LLM panel usually means one shared framing rather than a safe trade."""
    block = _tally([_op("quant", "bullish"), _op("risk", "bullish"),
                    _op("sentiment", "bullish")])
    assert "unanim" in block.lower()


def test_the_block_never_tells_the_chair_what_to_decide():
    """It supplies the count. A line recommending a verdict would be the
    unfalsifiable override this deliberately is not."""
    block = _tally([_op("quant", "bullish"), _op("risk", "neutral")])
    for banned in ("you should", "therefore", "recommend", "take the trade",
                   "execute"):
        assert banned not in block.lower(), banned


def test_an_empty_table_produces_nothing():
    assert _tally([]) == ""


def test_the_block_reaches_the_chair():
    """Wiring, not content — a block the chair never sees is worthless."""
    import inspect
    src = inspect.getsource(RoundTable._synthesize)
    assert "_tally_block" in src
