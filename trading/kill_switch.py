"""Daily loss kill-switch.

`verification.criteria.max_daily_loss_usd` has existed since Phase 0 and
blocked nothing. This enforces it.

Four design decisions, each of which is the difference between a kill-switch
and a decoration:

1. **It measures drawdown from the day's opening equity, not realized P&L.**
   A position sitting $500 underwater has lost $500. Counting only closed
   trades lets the fund bleed all day and report a flat book.

2. **It latches.** Once tripped, it stays tripped for the rest of the trading
   day even if the market recovers. A switch that un-trips on a bounce is not
   a risk control, it is a dip buyer — and "we were only down 20% briefly" is
   exactly the day you need it to hold.

3. **It never blocks an exit.** Closing orders are always allowed. Blocking
   exits during a loss spiral would trap the fund in the position that caused
   the trip, which is the opposite of safety.

4. **It re-arms on a new trading day, and only then.** Manual re-arming within
   the same day requires an explicit `override_for_day` call, which is audited.
   There is deliberately no "reset()" that silently clears the day.

Days are keyed on the US/Eastern calendar date, the same basis as
`trading/pdt.py`, so a position opened Monday and stopped out Monday evening
belongs to Monday for both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from trading.sessions import EASTERN


@dataclass(frozen=True)
class KillSwitchVerdict:
    allowed: bool
    reason: str
    tripped: bool
    daily_pnl_usd: float
    limit_usd: float
    armed: bool                      # False when no equity has been observed yet

    @property
    def remaining_usd(self) -> float:
        """How much more the fund may lose today before the switch trips."""
        return max(0.0, self.limit_usd + min(0.0, self.daily_pnl_usd))


@dataclass
class DailyLossKillSwitch:
    """Tracks intraday drawdown and blocks new risk once the limit is hit."""

    max_daily_loss_usd: float
    opening_equity: dict[date, float] = field(default_factory=dict)
    latest_equity: dict[date, float] = field(default_factory=dict)
    tripped_days: set[date] = field(default_factory=set)
    overridden_days: set[date] = field(default_factory=set)
    trip_log: list[dict] = field(default_factory=list)

    # ---------- observation ----------

    def observe_equity(self, moment: datetime, equity_usd: float) -> None:
        """Record the book's mark-to-market value. Call this every tick.

        The first observation of a day sets that day's baseline, so the limit
        is measured against where the fund actually opened rather than against
        some historical high-water mark.
        """
        day = _day(moment)
        if day not in self.opening_equity:
            self.opening_equity[day] = equity_usd
        self.latest_equity[day] = equity_usd

        # Latch as soon as the breach happens, not only when an order arrives.
        if day not in self.tripped_days and self._breached(day):
            self.tripped_days.add(day)
            self.trip_log.append({
                "day": day.isoformat(),
                "at": moment.isoformat(),
                "opening_equity_usd": self.opening_equity[day],
                "equity_usd": equity_usd,
                "daily_pnl_usd": self.daily_pnl_usd(moment),
                "limit_usd": self.max_daily_loss_usd,
            })

    # ---------- reads ----------

    def daily_pnl_usd(self, moment: datetime) -> float:
        day = _day(moment)
        opening = self.opening_equity.get(day)
        latest = self.latest_equity.get(day)
        if opening is None or latest is None:
            return 0.0
        return latest - opening

    def is_armed(self, moment: datetime) -> bool:
        return _day(moment) in self.opening_equity

    def is_tripped(self, moment: datetime) -> bool:
        day = _day(moment)
        if day in self.overridden_days:
            return False
        return day in self.tripped_days

    # ---------- the gate ----------

    def evaluate(self, moment: datetime, *, is_closing: bool = False) -> KillSwitchVerdict:
        """May the fund take this order?

        `is_closing=True` is always allowed — see decision 3 in the module
        docstring. Pass it for any order that reduces an existing position.
        """
        day = _day(moment)
        pnl = self.daily_pnl_usd(moment)
        armed = self.is_armed(moment)
        tripped = self.is_tripped(moment)

        if is_closing:
            return KillSwitchVerdict(
                allowed=True,
                reason="closing orders are never blocked — the fund must be able to exit",
                tripped=tripped, daily_pnl_usd=pnl,
                limit_usd=self.max_daily_loss_usd, armed=armed,
            )

        if not armed:
            # We genuinely do not know the day's P&L yet. Failing closed here
            # would mean the fund can never place its first order of the day;
            # the loop observes equity before it trades, so this window is one
            # tick wide. `armed=False` makes the state visible rather than
            # silent.
            return KillSwitchVerdict(
                allowed=True,
                reason="kill-switch not yet armed — no equity observed for this day",
                tripped=False, daily_pnl_usd=0.0,
                limit_usd=self.max_daily_loss_usd, armed=False,
            )

        if tripped:
            return KillSwitchVerdict(
                allowed=False,
                reason=(
                    f"DAILY LOSS LIMIT: down ${abs(pnl):,.2f} against a "
                    f"${self.max_daily_loss_usd:,.2f} limit on {day.isoformat()}. "
                    "No new risk today. Exits remain open."
                ),
                tripped=True, daily_pnl_usd=pnl,
                limit_usd=self.max_daily_loss_usd, armed=True,
            )

        return KillSwitchVerdict(
            allowed=True,
            reason=f"within daily loss budget (P&L ${pnl:,.2f})",
            tripped=False, daily_pnl_usd=pnl,
            limit_usd=self.max_daily_loss_usd, armed=True,
        )

    # ---------- manual intervention ----------

    def override_for_day(self, moment: datetime, reason: str) -> dict:
        """Re-arm within the same day. Deliberately explicit and audited.

        There is no silent reset: overriding a tripped kill-switch is a
        decision a human makes on the record, not a state a loop can clear.
        """
        if not reason or not reason.strip():
            raise ValueError("an override must carry a stated reason")
        day = _day(moment)
        self.overridden_days.add(day)
        record = {
            "day": day.isoformat(),
            "at": moment.isoformat(),
            "reason": reason.strip(),
            "daily_pnl_usd": self.daily_pnl_usd(moment),
        }
        self.trip_log.append({**record, "event": "override"})
        return record

    # ---------- status ----------

    def status(self, moment: datetime) -> dict:
        pnl = self.daily_pnl_usd(moment)
        return {
            "day": _day(moment).isoformat(),
            "armed": self.is_armed(moment),
            "tripped": self.is_tripped(moment),
            "overridden": _day(moment) in self.overridden_days,
            "daily_pnl_usd": round(pnl, 2),
            "limit_usd": self.max_daily_loss_usd,
            "remaining_usd": round(max(0.0, self.max_daily_loss_usd + min(0.0, pnl)), 2),
            "opening_equity_usd": self.opening_equity.get(_day(moment)),
        }

    # ---------- internals ----------

    def _breached(self, day: date) -> bool:
        opening = self.opening_equity.get(day)
        latest = self.latest_equity.get(day)
        if opening is None or latest is None:
            return False
        return (latest - opening) <= -self.max_daily_loss_usd


def _day(moment: datetime) -> date:
    if moment.tzinfo is None:
        raise ValueError("naive datetime rejected — the trading day is Eastern-time based")
    return moment.astimezone(EASTERN).date()
