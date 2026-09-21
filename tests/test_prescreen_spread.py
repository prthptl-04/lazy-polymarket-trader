"""A spread the grader will certainly refuse must not buy six LLM calls.

The pre-screen exists so that "anything disqualifying on arithmetic alone
should never reach [a deliberation]" — its own docstring. It checked the exit
plan and Altman distress, and not the spread. So a weekend BTC candidate at
Robinhood's live 187bps quote did this:

    prescreen worth_debating=True   passed deterministic pre-screen
    ...six seat calls and a chair call...
    grader: passed=False  rule=max_spread_bps
    reason: spread 187bps exceeds 50bps limit for the crypto_only session

Seven LLM calls per candidate, per cycle, for an outcome that was arithmetic
from the start. The spread is known before the table convenes and the limit is
a constant, so this is the cheapest possible veto and it was missing.

The limit is read from the SAME criteria the grader uses, through the same
session rule, so the two cannot drift apart into a pre-screen that passes what
the grader refuses — which is exactly the state this fixes.
"""

import pytest

from trading.candidate_builder import build_candidate
from finance.exits import Bar
from verification.criteria import DEFAULT_CRITERIA
from verification.outcome_grader import OutcomeGrader, DirectionalTrade
from trading.pipeline import _slippage_estimate

BARS = [Bar(high=81500, low=80500, close=81000) for _ in range(30)]


def _built(spread_bps, session="crypto_only", **kw):
    return build_candidate(
        symbol="BTC", bars=BARS, price=81000.0, asset_class="crypto",
        session=session, spread_bps=spread_bps,
        returns=[0.004] * 30, dollar_volumes=[5e9] * 30, **kw)


def test_a_spread_the_grader_will_refuse_is_vetoed_before_the_table():
    """The regression: Robinhood's live weekend BTC spread."""
    built = _built(187)
    assert not built.prescreen.worth_debating
    assert built.prescreen.rejected_by == "max_spread_bps"
    assert "187" in built.prescreen.reason


def test_a_tradable_spread_still_reaches_the_table():
    """No false vetoes — an equity at 3bps must still be debated."""
    built = build_candidate(
        symbol="AAPL", bars=[Bar(high=101, low=99, close=100) for _ in range(30)],
        price=100.0, asset_class="equity", session="regular", spread_bps=3,
        returns=[0.005] * 30, dollar_volumes=[5e8] * 30)
    assert built.prescreen.worth_debating


def test_the_veto_uses_the_same_limit_the_grader_does():
    """A pre-screen that passes what the grader refuses is the bug itself.
    Anything the pre-screen admits must survive the grader's spread check."""
    grader = OutcomeGrader(DEFAULT_CRITERIA)
    for spread in (1, 25, 49, 50, 51, 100, 187, 400):
        built = _built(spread)
        if not built.prescreen.worth_debating:
            continue
        plan = built.exit_plan
        trade = DirectionalTrade(
            symbol="BTC", side="buy", size_usd=100.0, entry=81000.0,
            stop=plan.stop, target=plan.target, win_probability=0.65,
            asset_class="crypto", spread_bps=spread,
            estimated_slippage_bps=_slippage_estimate(built.candidate),
            session="crypto_only", is_entry=True)
        result = grader.evaluate(trade)
        assert result.rejected_rule != "max_spread_bps", (
            f"prescreen admitted {spread}bps that the grader then refused")


def test_extended_hours_gets_the_wider_limit():
    """Premarket books are thin; the grader allows more there and so must this."""
    assert _built(75, session="premarket").prescreen.worth_debating
    assert not _built(75, session="regular").prescreen.worth_debating


def test_an_unknown_spread_is_not_vetoed():
    """Absent is not wide. A missing spread is reported by other means; guessing
    it is over the limit would veto every candidate on a feed that went quiet."""
    assert _built(None).prescreen.worth_debating
