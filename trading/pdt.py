"""Pattern Day Trader gate.

FINRA rule: an account under $25,000 equity that executes **4 or more day
trades within 5 rolling business days** is flagged as a Pattern Day Trader and
gets restricted — typically frozen to closing-only for 90 days, or until the
balance is topped up.

For an autonomous fund on a sub-$25k account this is the binding constraint on
the whole equities strategy. It is not advisory telemetry: hitting it takes the
fund offline for a quarter. So this gate **blocks**, and it blocks on the trade
that *would be* the fourth — not after the fact.

A "day trade" is a round trip in the same security on the same trading day:
open then close (or short then cover). Buying Monday and selling Tuesday is not
a day trade. Partial closes of a same-day position still count as one.

**Crypto is exempt** — FINRA's rule covers securities, not crypto. That is
precisely why the fund puts weekends on crypto: the weekend venue carries no
day-trade budget at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable, Literal

from trading.sessions import EASTERN, is_trading_day


PDT_EQUITY_THRESHOLD_USD = 25_000.0
PDT_MAX_DAY_TRADES = 3            # the 4th in the window is what flags you
PDT_WINDOW_BUSINESS_DAYS = 5

AssetClass = Literal["equity", "crypto"]


@dataclass(frozen=True)
class DayTrade:
    """One completed same-day round trip."""

    symbol: str
    trading_day: date
    asset_class: AssetClass = "equity"


@dataclass(frozen=True)
class PDTVerdict:
    allowed: bool
    reason: str
    day_trades_used: int
    day_trades_remaining: int
    would_be_day_trade: bool


@dataclass
class DayTradeTracker:
    """Counts day trades in the rolling window and gates new closes.

    `account_equity_usd` is refreshed from the broker; the gate disengages
    entirely at or above $25k, matching how the rule actually works.
    """

    account_equity_usd: float = 0.0
    trades: list[DayTrade] = field(default_factory=list)
    # symbol -> set of trading days on which we opened a position
    _opens: dict[str, set[date]] = field(default_factory=dict)

    # ---------- persistence ----------

    def snapshot(self, *, today: date) -> dict:
        """The ledger, pruned to the rolling window.

        `account_equity_usd` is deliberately absent. It is refreshed from the
        broker every boot, and a stale $26,000 would disengage the gate
        entirely — the one field here where persisting is actively dangerous.

        `asset_class` is dropped because the only `trades.append` site
        hardcodes "equity" and `record_close` returns early for crypto, so
        there is nothing else it could be.
        """
        floor = window_start(today)
        return {
            "trades": [[t.symbol, t.trading_day.isoformat()]
                       for t in self.trades if t.trading_day >= floor],
            "opens": {sym: sorted(d.isoformat() for d in days if d >= floor)
                      for sym, days in self._opens.items()
                      if any(d >= floor for d in days)},
        }

    def restore(self, data: Optional[dict]) -> None:
        """Rebuild the ledger. Losing it costs a 90-day PDT restriction."""
        if not data:
            return
        for row in data.get("trades") or []:
            try:
                symbol, iso = row[0], row[1]
                day = date.fromisoformat(str(iso))
            except (TypeError, ValueError, IndexError):
                continue
            self.trades.append(DayTrade(symbol=str(symbol), trading_day=day,
                                        asset_class="equity"))
        for symbol, days in (data.get("opens") or {}).items():
            for iso in days:
                try:
                    self._opens.setdefault(str(symbol), set()).add(
                        date.fromisoformat(str(iso)))
                except (TypeError, ValueError):
                    continue

    # ---------- recording ----------

    def record_open(self, symbol: str, moment: datetime) -> None:
        day = _trading_day_of(moment)
        self._opens.setdefault(symbol, set()).add(day)

    def record_close(
        self, symbol: str, moment: datetime, *, asset_class: AssetClass = "equity"
    ) -> DayTrade | None:
        """Record a close. Returns a DayTrade if this completed a round trip."""
        day = _trading_day_of(moment)
        if asset_class == "crypto":
            return None
        if day not in self._opens.get(symbol, set()):
            return None                    # closing a position opened earlier
        trade = DayTrade(symbol=symbol, trading_day=day, asset_class="equity")
        self.trades.append(trade)
        return trade

    # ---------- the gate ----------

    def evaluate_close(
        self,
        symbol: str,
        moment: datetime,
        *,
        asset_class: AssetClass = "equity",
    ) -> PDTVerdict:
        """Would closing `symbol` right now trip the PDT rule?

        Call this BEFORE submitting a closing order. `allowed=False` means the
        order must not be sent.
        """
        used = self.day_trades_in_window(moment)
        remaining = max(0, PDT_MAX_DAY_TRADES - used)

        if asset_class == "crypto":
            return PDTVerdict(
                allowed=True,
                reason="crypto is exempt from the PDT rule",
                day_trades_used=used,
                day_trades_remaining=remaining,
                would_be_day_trade=False,
            )

        if self.account_equity_usd >= PDT_EQUITY_THRESHOLD_USD:
            return PDTVerdict(
                allowed=True,
                reason=(
                    f"account equity ${self.account_equity_usd:,.0f} is at or above "
                    f"the ${PDT_EQUITY_THRESHOLD_USD:,.0f} threshold"
                ),
                day_trades_used=used,
                day_trades_remaining=remaining,
                would_be_day_trade=False,
            )

        day = _trading_day_of(moment)
        would_be = day in self._opens.get(symbol, set())
        if not would_be:
            return PDTVerdict(
                allowed=True,
                reason="closing a position opened on an earlier day — not a day trade",
                day_trades_used=used,
                day_trades_remaining=remaining,
                would_be_day_trade=False,
            )

        if used >= PDT_MAX_DAY_TRADES:
            return PDTVerdict(
                allowed=False,
                reason=(
                    f"BLOCKED: this would be day trade #{used + 1} in {PDT_WINDOW_BUSINESS_DAYS} "
                    f"business days on a ${self.account_equity_usd:,.0f} account. "
                    "Flagging as a Pattern Day Trader restricts the account for 90 days. "
                    "Hold the position overnight instead."
                ),
                day_trades_used=used,
                day_trades_remaining=0,
                would_be_day_trade=True,
            )

        return PDTVerdict(
            allowed=True,
            reason=f"day trade {used + 1} of {PDT_MAX_DAY_TRADES} in the rolling window",
            day_trades_used=used,
            day_trades_remaining=remaining - 1,
            would_be_day_trade=True,
        )

    # ---------- reads ----------

    def day_trades_in_window(self, moment: datetime) -> int:
        return len(list(self._window_trades(moment)))

    def _window_trades(self, moment: datetime) -> Iterable[DayTrade]:
        start = window_start(_trading_day_of(moment))
        for t in self.trades:
            if start <= t.trading_day <= _trading_day_of(moment):
                yield t

    def status(self, moment: datetime) -> dict:
        used = self.day_trades_in_window(moment)
        return {
            "account_equity_usd": self.account_equity_usd,
            "pdt_applies": self.account_equity_usd < PDT_EQUITY_THRESHOLD_USD,
            "day_trades_used": used,
            "day_trades_remaining": max(0, PDT_MAX_DAY_TRADES - used),
            "window_start": window_start(_trading_day_of(moment)).isoformat(),
        }


def window_start(day: date) -> date:
    """First day of the 5-business-day rolling window ending on `day`."""
    counted = 1 if is_trading_day(day) else 0
    cursor = day
    guard = 0
    while counted < PDT_WINDOW_BUSINESS_DAYS and guard < 30:
        cursor -= timedelta(days=1)
        guard += 1
        if is_trading_day(cursor):
            counted += 1
    return cursor


def _trading_day_of(moment: datetime) -> date:
    if moment.tzinfo is None:
        raise ValueError("naive datetime rejected — PDT windows are Eastern-time based")
    return moment.astimezone(EASTERN).date()
