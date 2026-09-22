"""A per-position risk budget is not a portfolio risk budget.

Measured 2026-09-22 07:47, five open positions:

    symbol      value   stop%   risk$
    SMH         58.51    6.3%    3.67
    PEPE-USD    29.17   13.7%    4.00
    ARM        139.62   11.4%   15.96
    META       115.16    7.1%    8.21
    SNDK        39.74   13.1%    5.22
                              -------
    planned loss if every stop fires      $37.06
    daily loss limit                      $50.00      = 74%

An hour earlier that total was $22.54. Each position was individually prudent —
the sizer caps each one's risk at a fraction of the book — and nothing
aggregated them. Four of the five were semis or tech (SMH, ARM, SNDK, META), so
"every stop fires on the same day" is not a tail scenario in that book, it is a
Tuesday.

The kill switch enforces the daily limit by watching equity, which means it
reacts AFTER the money is gone. It cannot stop a book being ASSEMBLED whose own
plan, fully executed, breaches the limit. This is the other half of it: do not
take risk the day cannot pay for.

A TIGHTENING. No threshold moved — the $50 limit is the same $50 the kill
switch already used, read from the same place (rule #11: it lives in
config/fund.toml and reaches the fund through FundConfig, never a second copy).
"""

import pytest

from finance.exits import ExitPlan
from finance.sizing import size_position


def _plan(entry=100.0, stop=90.0):
    return ExitPlan(entry=entry, stop=stop, target=entry * 1.15,
                    direction="long", atr=entry * 0.05)


def _size(committed=0.0, limit=50.0, **kw):
    return size_position(
        win_probability=0.62, plan=_plan(), bankroll_usd=10_000.0,
        available_cash_usd=10_000.0, open_positions=1, max_position_usd=10_000.0,
        portfolio_risk_usd=committed, portfolio_risk_limit_usd=limit, **kw)


def test_an_empty_book_is_bound_by_the_whole_limit():
    """$50 of room at $10 of risk per unit priced at $100 is 5 units, $500."""
    s = _size(committed=0.0)
    assert s.binding_constraint == "portfolio_risk"
    assert s.size_usd == pytest.approx(500.0)


def test_risk_already_committed_reduces_the_room():
    s = _size(committed=40.0)
    assert s.size_usd == pytest.approx(100.0), "only $10 of risk left"
    assert s.risk_usd == pytest.approx(10.0, abs=0.01)


def test_the_measured_book_still_has_room_but_less():
    """The live figure: $37.06 committed of $50."""
    s = _size(committed=37.06)
    assert s.is_actionable
    assert s.risk_usd == pytest.approx(12.94, abs=0.05)


def test_a_book_at_the_limit_takes_no_more_risk():
    s = _size(committed=50.0)
    assert not s.is_actionable
    assert "already risks" in s.reason and "daily limit" in s.reason


def test_a_book_past_the_limit_is_not_topped_up_on_negative_arithmetic():
    s = _size(committed=75.0)
    assert not s.is_actionable
    assert s.size_usd == 0.0


def test_a_tighter_constraint_still_binds_first():
    """The portfolio budget is one candidate among several, not an override."""
    s = size_position(
        win_probability=0.62, plan=_plan(), bankroll_usd=10_000.0,
        available_cash_usd=25.0, open_positions=1, max_position_usd=10_000.0,
        portfolio_risk_usd=0.0, portfolio_risk_limit_usd=50.0)
    assert s.binding_constraint == "cash"


def test_no_limit_configured_leaves_sizing_untouched():
    """Every existing caller passes nothing and must be unaffected."""
    a = size_position(win_probability=0.62, plan=_plan(), bankroll_usd=10_000.0,
                      available_cash_usd=10_000.0, open_positions=1,
                      max_position_usd=150.0)
    assert a.binding_constraint == "max_position"
    assert a.size_usd == pytest.approx(150.0)


# ---------- the fund measures the book it actually holds ----------

class _Plan:
    def __init__(self, stop): self.stop = stop


class _Pos:
    def __init__(self, qty, entry, stop):
        self.quantity, self.entry_price, self.plan = qty, entry, _Plan(stop)
        self.stop = stop


class _Book:
    def __init__(self, **positions): self.positions = positions


def _fund(book):
    from trading.fund import FundLoop
    f = object.__new__(FundLoop)
    f.position_book = book
    return f


def test_the_fund_sums_risk_at_the_stops():
    from trading.fund import FundLoop

    book = _Book(ARM=_Pos(0.170079, 321.86, 284.032),
                 META=_Pos(0.099582, 739.546, 685.966))
    total = FundLoop._portfolio_risk_usd(_fund(book))
    # 0.170079 x 37.828 + 0.099582 x 53.580
    assert total == pytest.approx(6.434 + 5.336, abs=0.01)


def test_an_empty_book_risks_nothing():
    from trading.fund import FundLoop
    assert FundLoop._portfolio_risk_usd(_fund(_Book())) == 0.0
    assert FundLoop._portfolio_risk_usd(_fund(None)) == 0.0


def test_a_malformed_position_is_skipped_not_fatal():
    """This runs per candidate inside a cycle. A sizing helper that can halt
    the fund by raising is a worse failure than one that lets a trade past."""
    from trading.fund import FundLoop

    bad = _Pos(0.1, 100.0, 90.0)
    bad.quantity = "not a number"
    book = _Book(GOOD=_Pos(1.0, 100.0, 90.0), BAD=bad)
    assert FundLoop._portfolio_risk_usd(_fund(book)) == pytest.approx(10.0)


def test_the_cycle_passes_the_book_risk_and_the_kill_switch_limit():
    """Rule #11: the limit lives in config/fund.toml and reaches the fund
    through FundConfig. Read it off the kill switch, never a second copy."""
    import inspect

    from trading.fund import FundLoop
    src = inspect.getsource(FundLoop)
    assert "portfolio_risk_usd=self._portfolio_risk_usd()" in src
    assert 'getattr(\n                self.kill_switch, "max_daily_loss_usd", None)' in src
