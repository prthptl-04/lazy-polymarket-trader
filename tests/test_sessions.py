"""Market session calendar.

- Acceptance: the Mon-Fri equities / weekend crypto rotation.
- Edge: session boundaries to the minute, holidays, early closes, DST.
- Blind: naive datetimes must be refused, not silently coerced.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading.sessions import (
    EASTERN,
    Session,
    equities_tradable,
    is_trading_day,
    is_weekend_crypto_window,
    next_session_change,
    session_at,
    should_flatten_crypto,
)

UTC = ZoneInfo("UTC")


def et(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=EASTERN)


# ---------------- weekday sessions ----------------

@pytest.mark.parametrize("hh,mm,expected", [
    (3, 59, Session.CRYPTO_ONLY),
    (4, 0, Session.PREMARKET),
    (9, 29, Session.PREMARKET),
    (9, 30, Session.REGULAR),
    (15, 59, Session.REGULAR),
    (16, 0, Session.AFTER_HOURS),
    (19, 59, Session.AFTER_HOURS),
    (20, 0, Session.CRYPTO_ONLY),
    (23, 30, Session.CRYPTO_ONLY),
])
def test_weekday_session_boundaries(hh, mm, expected):
    # 2026-09-16 is a Wednesday.
    assert session_at(et(2026, 9, 16, hh, mm)) is expected


def test_equities_open_flags():
    assert Session.REGULAR.equities_open
    assert Session.PREMARKET.equities_open
    assert not Session.CRYPTO_ONLY.equities_open


def test_extended_hours_flag():
    assert Session.PREMARKET.is_extended_hours
    assert Session.AFTER_HOURS.is_extended_hours
    assert not Session.REGULAR.is_extended_hours


# ---------------- weekends ----------------

def test_saturday_is_crypto_only():
    assert session_at(et(2026, 9, 19, 12, 0)) is Session.CRYPTO_ONLY


def test_sunday_is_crypto_only():
    assert session_at(et(2026, 9, 20, 12, 0)) is Session.CRYPTO_ONLY


def test_friday_evening_starts_the_crypto_window():
    assert is_weekend_crypto_window(et(2026, 9, 19, 2, 0))     # Saturday
    assert not is_weekend_crypto_window(et(2026, 9, 18, 21, 0))  # Friday night, weekday


def test_equities_tradable_helper():
    assert equities_tradable(et(2026, 9, 16, 10, 0))
    assert not equities_tradable(et(2026, 9, 19, 10, 0))


# ---------------- holidays + early closes ----------------

def test_labor_day_is_crypto_only():
    assert session_at(et(2026, 9, 7, 11, 0)) is Session.CRYPTO_ONLY
    assert not is_trading_day(date(2026, 9, 7))


def test_christmas_is_crypto_only():
    assert session_at(et(2026, 12, 25, 11, 0)) is Session.CRYPTO_ONLY


def test_early_close_day_shuts_at_one_pm():
    # 2026-11-27, day after Thanksgiving.
    assert session_at(et(2026, 11, 27, 12, 59)) is Session.REGULAR
    assert session_at(et(2026, 11, 27, 13, 0)) is Session.CRYPTO_ONLY


def test_early_close_has_no_after_hours():
    assert session_at(et(2026, 11, 27, 17, 0)) is Session.CRYPTO_ONLY


def test_normal_day_still_has_after_hours():
    assert session_at(et(2026, 11, 30, 17, 0)) is Session.AFTER_HOURS


# ---------------- timezone correctness ----------------

def test_naive_datetime_is_refused():
    with pytest.raises(ValueError, match="naive"):
        session_at(datetime(2026, 9, 16, 10, 0))


def test_utc_input_is_converted_not_misread():
    # 14:00 UTC == 10:00 EDT → regular session.
    assert session_at(datetime(2026, 9, 16, 14, 0, tzinfo=UTC)) is Session.REGULAR


def test_dst_boundary_uses_wall_clock_not_offset():
    # DST ends 2026-11-01. On 11-02 (Mon) 09:30 EST must still be the open.
    assert session_at(et(2026, 11, 2, 9, 29)) is Session.PREMARKET
    assert session_at(et(2026, 11, 2, 9, 30)) is Session.REGULAR


# ---------------- next transition ----------------

def test_next_change_from_premarket_is_the_open():
    nxt = next_session_change(et(2026, 9, 16, 5, 0))
    assert nxt == et(2026, 9, 16, 9, 30)


def test_next_change_from_saturday_is_monday_premarket():
    nxt = next_session_change(et(2026, 9, 19, 12, 0))
    assert nxt == et(2026, 9, 21, 4, 0)


def test_next_change_skips_a_holiday():
    # Fri 2026-09-04 after close → next equity session is Tue 09-08 (Mon is Labor Day).
    nxt = next_session_change(et(2026, 9, 4, 21, 0))
    assert nxt == et(2026, 9, 8, 4, 0)


def test_next_change_always_advances():
    moment = et(2026, 9, 16, 5, 0)
    nxt = next_session_change(moment)
    assert nxt > moment


# ---------------- weekend flatten ----------------

def test_flatten_triggers_inside_the_lead_window():
    # Monday premarket opens 04:00; 03:30 Sunday-night is inside 60 min.
    assert should_flatten_crypto(et(2026, 9, 21, 3, 30))


def test_flatten_does_not_trigger_early_in_the_weekend():
    assert not should_flatten_crypto(et(2026, 9, 19, 12, 0))


def test_flatten_false_during_equity_hours():
    assert not should_flatten_crypto(et(2026, 9, 16, 10, 0))


def test_flatten_accounts_for_a_monday_holiday():
    # Labor Day Mon 2026-09-07 → next open is Tue 09-08 04:00.
    assert not should_flatten_crypto(et(2026, 9, 7, 3, 30))
    assert should_flatten_crypto(et(2026, 9, 8, 3, 30))
