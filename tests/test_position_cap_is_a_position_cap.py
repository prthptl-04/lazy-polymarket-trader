"""`max_position_usd` caps the POSITION, not each order that builds it.

Measured 2026-09-22 07:37, after four premarket cycles and four submissions:

    symbol         quantity       entry     value       fills
    ARM            0.170079      321.86     54.74           2
    META           0.099582      739.55     73.65           2

ARM's second add (0.087965) was LARGER than its first (0.082114). Every order
was sized as though the book were flat: `held_quantity` reached
`ThesisPipeline.run`, but it was used only to decide whether a BEARISH signal
should close the position. It never reached the sizer. So a bullish thesis on a
name already held produced a fresh, full-size order capped at $150 — the cap
applying to the increment, and resetting every cycle.

A $150 cap that resets on every cycle is not a position cap. It is a cap on how
fast the position grows. Cash had gone $500 -> $230.75 in a few hours across
five names, and nothing in the system would have stopped one of them absorbing
the rest.

This is a TIGHTENING and no threshold moved: $150 is still $150, it now means
what it says.
"""

import pytest

from finance.exits import ExitPlan
from finance.sizing import size_position


def _plan(entry=100.0):
    return ExitPlan(entry=entry, stop=entry * 0.9, target=entry * 1.15,
                    direction="long", atr=entry * 0.05)


def _size(existing=0.0, cap=150.0, cash=10_000.0):
    return size_position(
        win_probability=0.62, plan=_plan(), bankroll_usd=10_000.0,
        available_cash_usd=cash, open_positions=1,
        max_position_usd=cap, existing_position_usd=existing)


def test_a_flat_book_gets_the_whole_cap():
    s = _size(existing=0.0)
    assert s.binding_constraint == "max_position"
    assert s.size_usd == pytest.approx(150.0)


def test_an_existing_holding_leaves_only_the_room_that_is_left():
    s = _size(existing=100.0)
    assert s.size_usd == pytest.approx(50.0), "cap minus what is already held"


def test_a_full_position_cannot_be_added_to():
    s = _size(existing=150.0)
    assert not s.is_actionable
    assert s.size_usd == 0.0
    assert "already holding" in s.reason and "cap" in s.reason


def test_an_overweight_position_cannot_be_added_to_either():
    """A name already past the cap — from a fill at a worse price, say — must
    not be topped up because the arithmetic went negative."""
    s = _size(existing=400.0)
    assert not s.is_actionable
    assert s.size_usd == 0.0


def test_the_observed_compounding_is_now_refused():
    """The live case. ARM held $54.74; a third add of the same size would have
    taken it past nothing at all before, and is now bounded."""
    first = _size(existing=0.0)
    second = _size(existing=first.size_usd)
    third = _size(existing=first.size_usd + second.size_usd)
    total = first.size_usd + second.size_usd + third.size_usd
    assert total <= 150.0 + 1e-6, f"the position must never exceed the cap: {total}"
    assert not third.is_actionable


def test_a_tighter_constraint_still_wins():
    """The cap is one candidate among several. Cash, Kelly, risk budget and
    concentration must still be able to bind first."""
    s = _size(existing=0.0, cash=20.0)
    assert s.binding_constraint == "cash"
    assert s.size_usd == pytest.approx(20.0)


def test_no_cap_configured_is_unchanged():
    s = size_position(win_probability=0.62, plan=_plan(), bankroll_usd=10_000.0,
                      available_cash_usd=10_000.0, open_positions=1,
                      max_position_usd=None, existing_position_usd=99.0)
    assert s.binding_constraint != "max_position"


def test_the_default_is_flat_so_every_old_caller_is_unaffected():
    a = size_position(win_probability=0.62, plan=_plan(), bankroll_usd=10_000.0,
                      max_position_usd=150.0)
    b = _size(existing=0.0, cash=None) if False else a
    assert a.size_usd == pytest.approx(150.0)
    assert b.size_usd == pytest.approx(150.0)


# ---------- the pipeline actually passes it ----------

def test_the_pipeline_passes_the_held_value_to_the_sizer():
    """`held_quantity` reached the pipeline all along and was used only for the
    bearish close. A sizer that is never told is a cap that never applies."""
    import inspect

    from trading.pipeline import ThesisPipeline
    src = inspect.getsource(ThesisPipeline.run)
    assert "existing_position_usd=held_quantity" in src

    src = inspect.getsource(ThesisPipeline._size_for)
    assert "existing_position_usd=existing_position_usd" in src


def test_the_held_value_is_marked_not_carried_at_entry():
    """Exposure is what the name is worth now, not what it cost."""
    import inspect

    from trading.pipeline import ThesisPipeline
    src = inspect.getsource(ThesisPipeline.run)
    assert "candidate.price" in src.split("existing_position_usd=held_quantity")[1][:80]
