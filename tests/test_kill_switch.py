"""Daily loss kill-switch.

Declared in criteria since Phase 0, enforced as of now.

- Acceptance: trips at the limit, blocks new risk, re-arms next day.
- Blind (the ones that make it a real control):
    * it LATCHES — a recovery must not un-trip it
    * it NEVER blocks an exit
    * it measures unrealized drawdown, not just realized P&L
- Edge: unarmed state, naive datetimes, explicit override.
"""

from datetime import datetime, timedelta

import pytest

from trading.kill_switch import DailyLossKillSwitch
from trading.sessions import EASTERN


def et(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=EASTERN)


MON = et(2026, 9, 14)
TUE = et(2026, 9, 15)


def _ks(limit=100.0):
    return DailyLossKillSwitch(max_daily_loss_usd=limit)


# ---------------- basic tripping ----------------

def test_within_budget_is_allowed():
    ks = _ks()
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_950.0)
    v = ks.evaluate(MON + timedelta(hours=1))
    assert v.allowed and not v.tripped
    assert v.daily_pnl_usd == pytest.approx(-50.0)


def test_trips_at_the_limit():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_900.0)     # exactly -100
    v = ks.evaluate(MON + timedelta(hours=1))
    assert not v.allowed and v.tripped
    assert "DAILY LOSS LIMIT" in v.reason


def test_trips_beyond_the_limit():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=2), 9_500.0)
    assert not ks.evaluate(MON + timedelta(hours=2)).allowed


def test_gains_never_trip():
    ks = _ks()
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 11_000.0)
    assert ks.evaluate(MON + timedelta(hours=1)).allowed


# ---------------- latching ----------------

def test_stays_tripped_after_a_recovery():
    """A switch that un-trips on a bounce is a dip buyer, not a risk control."""
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_800.0)     # trips
    ks.observe_equity(MON + timedelta(hours=2), 10_050.0)    # fully recovered

    v = ks.evaluate(MON + timedelta(hours=3))
    assert not v.allowed
    assert v.tripped


def test_trip_is_recorded_when_it_happens():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_800.0)

    assert len(ks.trip_log) == 1
    entry = ks.trip_log[0]
    assert entry["daily_pnl_usd"] == pytest.approx(-200.0)
    assert entry["limit_usd"] == 100.0


def test_trip_logged_once_not_per_tick():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    for i in range(1, 6):
        ks.observe_equity(MON + timedelta(hours=i), 9_700.0)
    assert len(ks.trip_log) == 1


# ---------------- exits are sacred ----------------

def test_closing_orders_are_allowed_while_tripped():
    """Blocking exits during a loss spiral traps us in the losing position."""
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_000.0)

    entry = ks.evaluate(MON + timedelta(hours=2))
    exit_ = ks.evaluate(MON + timedelta(hours=2), is_closing=True)

    assert not entry.allowed
    assert exit_.allowed
    assert "must be able to exit" in exit_.reason


def test_closing_verdict_still_reports_the_trip():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_000.0)
    v = ks.evaluate(MON + timedelta(hours=2), is_closing=True)
    assert v.allowed and v.tripped      # allowed, but honest about the state


# ---------------- unrealized losses count ----------------

def test_unrealized_drawdown_trips_it():
    """Equity is mark-to-market, so an open loser counts immediately."""
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    # No trade closed; the open position is simply marked down.
    ks.observe_equity(MON + timedelta(minutes=30), 9_850.0)
    assert ks.evaluate(MON + timedelta(minutes=30)).tripped


# ---------------- day boundaries ----------------

def test_new_day_re_arms():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_500.0)
    assert not ks.evaluate(MON + timedelta(hours=1)).allowed

    ks.observe_equity(TUE, 9_500.0)
    assert ks.evaluate(TUE).allowed


def test_each_day_gets_its_own_baseline():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_950.0)
    ks.observe_equity(TUE, 9_950.0)
    ks.observe_equity(TUE + timedelta(hours=1), 9_900.0)

    # Tuesday is only -50 against Tuesday's open, not -100 against Monday's.
    assert ks.daily_pnl_usd(TUE + timedelta(hours=1)) == pytest.approx(-50.0)
    assert ks.evaluate(TUE + timedelta(hours=1)).allowed


def test_baseline_is_the_first_observation_of_the_day():
    ks = _ks()
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 12_000.0)
    ks.observe_equity(MON + timedelta(hours=2), 11_000.0)
    # Measured from 10,000 — not from the 12,000 intraday high.
    assert ks.daily_pnl_usd(MON + timedelta(hours=2)) == pytest.approx(1_000.0)


# ---------------- unarmed ----------------

def test_unarmed_allows_but_says_so():
    ks = _ks()
    v = ks.evaluate(MON)
    assert v.allowed
    assert v.armed is False
    assert "not yet armed" in v.reason


def test_becomes_armed_on_first_observation():
    ks = _ks()
    assert not ks.is_armed(MON)
    ks.observe_equity(MON, 10_000.0)
    assert ks.is_armed(MON)


# ---------------- override ----------------

def test_override_re_arms_within_the_day():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_500.0)
    assert not ks.evaluate(MON + timedelta(hours=2)).allowed

    ks.override_for_day(MON + timedelta(hours=2), "manual review: data feed glitch")
    assert ks.evaluate(MON + timedelta(hours=2)).allowed


def test_override_requires_a_reason():
    ks = _ks()
    ks.observe_equity(MON, 10_000.0)
    with pytest.raises(ValueError):
        ks.override_for_day(MON, "   ")


def test_override_is_audited():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_500.0)
    ks.override_for_day(MON + timedelta(hours=2), "reviewed by operator")

    events = [e for e in ks.trip_log if e.get("event") == "override"]
    assert len(events) == 1
    assert "reviewed by operator" in events[0]["reason"]


def test_override_does_not_leak_into_the_next_day():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_500.0)
    ks.override_for_day(MON + timedelta(hours=2), "reviewed")

    ks.observe_equity(TUE, 9_500.0)
    ks.observe_equity(TUE + timedelta(hours=1), 9_000.0)
    assert not ks.evaluate(TUE + timedelta(hours=1)).allowed


# ---------------- status + hygiene ----------------

def test_status_shape():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_960.0)
    s = ks.status(MON + timedelta(hours=1))

    assert s["armed"] is True
    assert s["tripped"] is False
    assert s["daily_pnl_usd"] == pytest.approx(-40.0)
    assert s["remaining_usd"] == pytest.approx(60.0)


def test_remaining_is_zero_once_tripped():
    ks = _ks(limit=100.0)
    ks.observe_equity(MON, 10_000.0)
    ks.observe_equity(MON + timedelta(hours=1), 9_800.0)
    assert ks.status(MON + timedelta(hours=1))["remaining_usd"] == 0.0


def test_naive_datetime_is_refused():
    ks = _ks()
    with pytest.raises(ValueError, match="naive"):
        ks.observe_equity(datetime(2026, 9, 14, 10, 0), 10_000.0)
