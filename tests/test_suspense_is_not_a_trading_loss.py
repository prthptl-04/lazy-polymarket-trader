"""The daily loss limit measures TRADING, not bookkeeping.

Measured 2026-09-22 05:37, premarket, after the fund had run five clean cycles
and submitted nothing:

    daily_pnl_usd   -46.33      of a  -50.00  limit
    remaining_usd     3.67
      of which  -43.75  was the booked unexplained gap
                 -1.72  realised
                 -0.86  marks

94% of the day's "loss" was an accounting artifact. The Risk Manager was shown
$3.67 of headroom and vetoed every equity entry on it — correctly, given what
it was shown. Its own words on ARM:

    "Daily loss headroom $3.67 of $50 — effectively exhausted; this alone
     vetoes the entry"

39 of 39 risk opinions bearish, 28 of 29 theses neutral, zero submitted. A
bookkeeping hole had switched the fund off, and every seat downstream was
behaving correctly about it.

What is NOT done here: the $50 limit is untouched, and no threshold moves. The
INPUT is narrowed to what the limit always claimed to measure. The safety
property that makes this safe rather than a loosening is that `absorb_gap` is
never called automatically and requires a stated reason — so an un-investigated
gap still counts against the limit in full, and only an amount a human has
looked at and named is excluded.

The asymmetry with sizing is deliberate: the suspense is NOT added back to the
bankroll the sizer uses, because that cash genuinely is not there.
"""

from datetime import datetime

import pytest
from zoneinfo import ZoneInfo

from trading.fund import FundLoop

ET = ZoneInfo("America/New_York")


class _Adapter:
    def __init__(self, unexplained=0.0):
        self.unexplained_usd = unexplained


class _Router:
    def __init__(self, *adapters): self.adapters = list(adapters)


def _fund(*adapters):
    f = object.__new__(FundLoop)
    f.router = _Router(*adapters)
    return f


def test_a_healthy_account_books_nothing():
    assert FundLoop._booked_suspense(_fund(_Adapter())) == 0.0


def test_the_booked_shortfall_is_summed():
    fund = _fund(_Adapter(-43.75), _Adapter(-1.25))
    assert FundLoop._booked_suspense(fund) == pytest.approx(-45.0)


def test_an_adapter_with_no_suspense_is_ignored():
    """A live adapter has no such field; it must not break the sum."""
    fund = _fund(_Adapter(-43.75), object())
    assert FundLoop._booked_suspense(fund) == pytest.approx(-43.75)


def test_a_nonsense_value_is_skipped_not_propagated():
    a = _Adapter()
    a.unexplained_usd = "not a number"
    assert FundLoop._booked_suspense(_fund(a, _Adapter(-10.0))) == pytest.approx(-10.0)


def test_no_router_is_zero():
    f = object.__new__(FundLoop)
    f.router = None
    assert FundLoop._booked_suspense(f) == 0.0


# ---------- the arithmetic that unblocked the fund ----------

def test_the_kill_switch_sees_trading_equity():
    """The live numbers. Equity 453.67 against a 500 open reads as -46.33 of a
    $50 limit; with the booked gap excluded it is -2.58, which is the day's
    actual trading."""
    from trading.kill_switch import DailyLossKillSwitch

    ks = DailyLossKillSwitch(max_daily_loss_usd=50.0)
    moment = datetime(2026, 9, 22, 5, 37, tzinfo=ET)
    ks.observe_equity(datetime(2026, 9, 22, 0, 1, tzinfo=ET), 500.0)

    fund = _fund(_Adapter(-43.75))
    ks.observe_equity(moment, 453.67 - FundLoop._booked_suspense(fund))

    assert ks.daily_pnl_usd(moment) == pytest.approx(-2.58, abs=0.01)
    assert ks.evaluate(moment).allowed, "the day is not a losing day"


def test_a_real_trading_loss_still_trips_the_switch():
    """The direction that matters. Excluding a booked artifact must not make
    the limit unreachable."""
    from trading.kill_switch import DailyLossKillSwitch

    ks = DailyLossKillSwitch(max_daily_loss_usd=50.0)
    open_at = datetime(2026, 9, 22, 0, 1, tzinfo=ET)
    moment = datetime(2026, 9, 22, 11, 0, tzinfo=ET)
    ks.observe_equity(open_at, 500.0)

    fund = _fund(_Adapter(-43.75))
    # Lost $60 TRADING, on top of the booked gap.
    ks.observe_equity(moment, 500.0 - 60.0 - 43.75 - FundLoop._booked_suspense(fund))

    assert ks.daily_pnl_usd(moment) == pytest.approx(-60.0, abs=0.01)
    assert not ks.evaluate(moment).allowed, "a real loss must still halt it"


def test_an_unbooked_gap_still_counts_in_full():
    """The safety property. Nothing is excluded until a human has investigated
    and named it — `absorb_gap` is never automatic."""
    from trading.kill_switch import DailyLossKillSwitch

    ks = DailyLossKillSwitch(max_daily_loss_usd=50.0)
    open_at = datetime(2026, 9, 22, 0, 1, tzinfo=ET)
    moment = datetime(2026, 9, 22, 11, 0, tzinfo=ET)
    ks.observe_equity(open_at, 500.0)

    fund = _fund(_Adapter(0.0))          # gap happened, NOT booked
    ks.observe_equity(moment, 445.0 - FundLoop._booked_suspense(fund))

    assert ks.daily_pnl_usd(moment) == pytest.approx(-55.0, abs=0.01)
    assert not ks.evaluate(moment).allowed


def test_the_cycle_uses_trading_equity_for_the_switch_only():
    """The sizer must still see the real bankroll: that cash is not there."""
    import inspect

    src = inspect.getsource(FundLoop.run_cycle)
    assert "equity_usd - self._booked_suspense()" in src
    i = src.index("self.pipeline.bankroll_usd = ")
    assert "_booked_suspense" not in src[i:i + 80], \
        "sizing stays on real equity, deliberately"
