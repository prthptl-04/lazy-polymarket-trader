"""A name that cannot be added to is not an opportunity.

Measured 2026-09-22 08:37. The screen returned the same ten names every cycle,
the same five were deliberated every cycle, and four of those five were already
held:

    ARM    $139.62 of a $150 cap
    META   $115.16
    SMH     $58.51
    SNDK    $39.74

Thirty-five model calls an hour re-deciding positions the sizer could not have
changed, while the other five names in the universe never got a deliberation
slot. Twenty consecutive theses came back neutral, which is the correct read on
a name you already own and cannot add to — the committee was not stuck, it was
being asked a question with only one answer.

`_drop_working` already skips a name with an order RESTING on it. This is its
sibling: skip a name whose position is full. Same reasoning, same shape, and
like that one it is not a cooldown — trim or close the position and the name is
tradable on the next cycle.

The test is EXACT — held at or above the cap — not a fraction of it. A
threshold for "nearly full" would be a number nobody chose, and the sizer
already refuses what it cannot fund.
"""

import pytest

from trading.fund import FundLoop


class _Pos:
    def __init__(self, qty, entry): self.quantity, self.entry_price = qty, entry


class _Criteria:
    def __init__(self, cap=150.0): self.max_position_usd = cap


class _Pipeline:
    def __init__(self, cap=150.0): self.criteria = _Criteria(cap)


def _fund(positions=None, cap=150.0, resting=()):
    f = object.__new__(FundLoop)
    f.position_book = type("B", (), {"positions": positions or {}})()
    f.pipeline = _Pipeline(cap)
    f.router = type("R", (), {"adapters": []})()
    f._resting_syms = set(resting)
    f._resting_symbols = lambda: set(f._resting_syms)
    return f


def test_a_full_position_loses_its_slot():
    fund = _fund({"ARM": _Pos(1.0, 150.0)})
    kept, skipped = FundLoop._drop_working(fund, ["ARM", "NVDA"])
    assert kept == ["NVDA"]
    assert skipped[0]["symbol"] == "ARM"
    assert "position cap" in skipped[0]["reason"]
    assert "150" in skipped[0]["reason"]


def test_a_position_with_room_keeps_its_slot():
    """ARM at $139.62 of $150 still has room, so it is still debated. The
    cutoff is the cap itself, not a guess at what counts as nearly full."""
    fund = _fund({"ARM": _Pos(0.170079 * 2.55, 321.86)})   # ~$139.62
    kept, _ = FundLoop._drop_working(fund, ["ARM"])
    assert kept == ["ARM"]


def test_an_overweight_position_also_loses_its_slot():
    fund = _fund({"ARM": _Pos(2.0, 150.0)})
    kept, skipped = FundLoop._drop_working(fund, ["ARM"])
    assert kept == [] and skipped


def test_the_reason_names_the_holding_and_the_cap():
    """A prescreen line a human reads should not need the code to interpret."""
    fund = _fund({"META": _Pos(1.0, 200.0)})
    _, skipped = FundLoop._drop_working(fund, ["META"])
    assert "$200.00" in skipped[0]["reason"]
    assert "$150.00" in skipped[0]["reason"]


def test_a_resting_order_still_takes_priority_in_the_reason():
    """Both can be true. The resting-order reason is the more actionable one —
    it clears on its own, the cap does not."""
    fund = _fund({"ARM": _Pos(1.0, 150.0)}, resting={"ARM"})
    _, skipped = FundLoop._drop_working(fund, ["ARM"])
    assert "resting" in skipped[0]["reason"]


def test_an_empty_book_changes_nothing():
    fund = _fund({})
    assert FundLoop._drop_working(fund, ["ARM", "NVDA"]) == (["ARM", "NVDA"], [])


def test_no_cap_configured_changes_nothing():
    """Without a cap there is no such thing as full."""
    fund = _fund({"ARM": _Pos(100.0, 150.0)}, cap=None)
    assert FundLoop._drop_working(fund, ["ARM"]) == (["ARM"], [])


def test_a_malformed_position_is_skipped_not_fatal():
    bad = _Pos("x", 150.0)
    fund = _fund({"ARM": bad, "META": _Pos(1.0, 150.0)})
    kept, skipped = FundLoop._drop_working(fund, ["ARM", "META"])
    assert kept == ["ARM"], "unreadable means we cannot say it is full"
    assert [s["symbol"] for s in skipped] == ["META"]


def test_it_is_not_a_cooldown():
    """Trim the position and the name is tradable on the very next cycle."""
    pos = _Pos(1.0, 150.0)
    fund = _fund({"ARM": pos})
    assert FundLoop._drop_working(fund, ["ARM"])[0] == []
    pos.quantity = 0.5
    assert FundLoop._drop_working(fund, ["ARM"])[0] == ["ARM"]


def test_the_scout_is_wide_enough_for_the_attrition_it_meets():
    """Measured on a universe of ten: five were rejected as Altman Z
    distressed before a seat was asked anything, and four of the five
    survivors were already held. A 2x scout assumed roughly half would
    survive; the solvency filter alone takes half, and holdings take more."""
    import inspect

    from dashboard import fund_wiring
    src = inspect.getsource(fund_wiring.build_fund)
    assert "max_candidates_per_cycle * 4" in src
