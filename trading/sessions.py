"""Market session calendar — what is tradable right now, and where.

The fund rotates: US equities Mon–Fri (premarket through after-hours), crypto
on weekends and holidays. This module is the single source of truth for that
rotation. It is pure logic — no network, no clock injection beyond the `now`
argument — so the scheduler, the round table, and the UI all agree.

All reasoning is in US/Eastern because that is what the exchanges use. Callers
pass timezone-aware datetimes; naive input is rejected rather than guessed at,
because a silent UTC-vs-Eastern mixup would trade at the wrong hour.

Hours (US/Eastern), per NYSE/Nasdaq extended sessions:

    04:00–09:30  premarket
    09:30–16:00  regular
    16:00–20:00  after-hours
    otherwise    equities closed

Crypto trades 24/7, so it is the venue whenever equities are shut.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo


EASTERN = ZoneInfo("America/New_York")

PREMARKET_OPEN = time(4, 0)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
AFTERHOURS_CLOSE = time(20, 0)


class Session(str, Enum):
    """What the fund may trade at a given instant."""

    PREMARKET = "premarket"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"
    CRYPTO_ONLY = "crypto_only"

    @property
    def equities_open(self) -> bool:
        return self is not Session.CRYPTO_ONLY

    @property
    def is_extended_hours(self) -> bool:
        """Thin liquidity, wider spreads — the round table demands more here."""
        return self in (Session.PREMARKET, Session.AFTER_HOURS)


# US market holidays when equities are shut (crypto keeps trading).
# Half-days (early 13:00 close) are listed separately — the market is open,
# just shorter, so they are not "closed" but they do move the close.
MARKET_HOLIDAYS: frozenset[date] = frozenset({
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Jr. Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
    # 2027
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 3, 26),   # Good Friday
    date(2027, 5, 31),
    date(2027, 6, 18),   # Juneteenth (observed)
    date(2027, 7, 5),    # Independence Day (observed)
    date(2027, 9, 6),
    date(2027, 11, 25),
    date(2027, 12, 24),  # Christmas (observed)
})

# Early closes: regular session ends 13:00 ET, no after-hours.
EARLY_CLOSE_DAYS: frozenset[date] = frozenset({
    date(2026, 11, 27),  # day after Thanksgiving
    date(2026, 12, 24),  # Christmas Eve
    date(2027, 11, 26),
})

EARLY_CLOSE_TIME = time(13, 0)


@dataclass(frozen=True)
class SessionWindow:
    session: Session
    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


def _require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError(
            "naive datetime rejected — pass a timezone-aware value so the "
            "session can't be computed against the wrong clock"
        )
    return moment.astimezone(EASTERN)


def is_trading_day(day: date) -> bool:
    """True when US equities trade at all on this calendar day."""
    return day.weekday() < 5 and day not in MARKET_HOLIDAYS


def regular_close_for(day: date) -> time:
    return EARLY_CLOSE_TIME if day in EARLY_CLOSE_DAYS else REGULAR_CLOSE


def session_at(moment: datetime) -> Session:
    """Which session is active at `moment`."""
    et = _require_aware(moment)
    if not is_trading_day(et.date()):
        return Session.CRYPTO_ONLY

    clock = et.time()
    close = regular_close_for(et.date())

    if clock < PREMARKET_OPEN:
        return Session.CRYPTO_ONLY
    if clock < REGULAR_OPEN:
        return Session.PREMARKET
    if clock < close:
        return Session.REGULAR
    # Early-close days have no after-hours session.
    if et.date() in EARLY_CLOSE_DAYS:
        return Session.CRYPTO_ONLY
    if clock < AFTERHOURS_CLOSE:
        return Session.AFTER_HOURS
    return Session.CRYPTO_ONLY


def crypto_tradable(moment: datetime) -> bool:
    """Crypto is always tradable. Explicit so callers read as intent."""
    _require_aware(moment)
    return True


def equities_tradable(moment: datetime) -> bool:
    return session_at(moment).equities_open


def next_session_change(moment: datetime, *, horizon_days: int = 10) -> datetime | None:
    """The next instant the session label changes.

    Used by the scheduler to sleep until something actually happens instead of
    polling. Returns None if nothing changes within `horizon_days` (which would
    mean a decade-long holiday, but the bound stops an infinite scan).
    """
    et = _require_aware(moment)
    current = session_at(et)
    limit = et + timedelta(days=horizon_days)

    # Session edges only ever land on these wall-clock times.
    candidates = (PREMARKET_OPEN, REGULAR_OPEN, EARLY_CLOSE_TIME, REGULAR_CLOSE, AFTERHOURS_CLOSE)
    cursor = et
    while cursor < limit:
        day = cursor.date()
        for t in sorted(candidates):
            edge = datetime.combine(day, t, tzinfo=EASTERN)
            if edge > et and session_at(edge) != current:
                return edge
        cursor = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=EASTERN)
    return None


def is_weekend_crypto_window(moment: datetime) -> bool:
    """True during the Fri-close → Mon-open stretch the fund gives to crypto."""
    et = _require_aware(moment)
    return session_at(et) is Session.CRYPTO_ONLY and not is_trading_day(et.date())


def next_equity_open(moment: datetime) -> datetime | None:
    """The next premarket open at or after `moment`."""
    et = _require_aware(moment)
    day = et.date()
    # "Later today" counts — the pre-04:00 stretch of a trading day is the
    # single most important flatten window, and skipping straight to tomorrow
    # would miss it entirely.
    for _ in range(10):
        if is_trading_day(day):
            open_at = datetime.combine(day, PREMARKET_OPEN, tzinfo=EASTERN)
            if open_at > et:
                return open_at
        day += timedelta(days=1)
    return None


def should_flatten_crypto(moment: datetime, *, lead_minutes: int = 60) -> bool:
    """True once we are inside `lead_minutes` of the next equity open.

    The fund closes weekend crypto before the next premarket so capital is free
    for equities, unless a thesis explicitly justifies carrying the position.

    Deliberately keyed on the session being crypto-only rather than on it being
    a weekend: the hours before 04:00 on a normal trading day are crypto-only
    too, and that is precisely when the handoff has to happen.
    """
    et = _require_aware(moment)
    if session_at(et) is not Session.CRYPTO_ONLY:
        return False
    open_at = next_equity_open(et)
    if open_at is None:
        return False
    return (open_at - et) <= timedelta(minutes=lead_minutes)
