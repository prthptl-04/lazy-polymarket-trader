"""Position lifecycle — the thing that actually enforces a stop.

Until this existed the fund computed a stop, sized against it, graded against
it, and then never looked at it again. The stop was paperwork. A position would
ride through it indefinitely, which is precisely the failure the stop was
supposed to prevent — and worse than having no stop, because the sizing math
assumed the loss was bounded.

This holds the open book and answers one question per tick: *has anything hit
its exit?* It produces `ExitSignal`s; it does not place orders. The caller
routes them through `VenueRouter` so exits pass the same gates as everything
else — with one asymmetry that matters: the kill-switch explicitly permits
closes, so a tripped day still lets positions out.

It is also where a thesis gets its verdict. Closing a position is the moment we
learn whether the committee was right, so `close()` writes the outcome that
`roundtable.calibration` scores. Without this, the scorecard stays empty
forever and `CONFIDENCE_SHRINK` can never stop being a guess.

Ordering rule for the caller: **check exits before considering new entries.**
A stop that fired must be honoured before tokens are spent on fresh ideas.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from finance.exits import ExitPlan, is_stop_breached, is_target_reached, trail_stop

logger = logging.getLogger(__name__)


ExitReason = Literal["stop", "target", "flatten", "manual"]


@dataclass
class ManagedPosition:
    """An open position and the plan it was opened under."""

    symbol: str
    asset_class: str
    quantity: float
    entry_price: float
    plan: ExitPlan
    thesis_id: Optional[str] = None
    # What the committee actually staked. Without these on the position, the
    # outcome row is written with NULL signal/confidence, and
    # fit_confidence_shrink discards 100% of rows at any sample size while
    # blaming the sample size — which is how a disconnected feedback loop looks
    # exactly like one that is merely waiting for data.
    signal: Optional[str] = None
    confidence: Optional[float] = None
    venue: Optional[str] = None
    opened_at: float = field(default_factory=time.time)

    def unrealized_return(self, price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.plan.direction == "long":
            return (price - self.entry_price) / self.entry_price
        return (self.entry_price - price) / self.entry_price

    def realized_return(self, exit_price: float) -> float:
        return self.unrealized_return(exit_price)


@dataclass(frozen=True)
class ExitSignal:
    position: ManagedPosition
    reason: ExitReason
    price: float

    @property
    def symbol(self) -> str:
        return self.position.symbol

    @property
    def quantity(self) -> float:
        return self.position.quantity

    def describe(self) -> str:
        return (
            f"{self.symbol}: {self.reason} at {self.price:.4f} "
            f"(entry {self.position.entry_price:.4f}, "
            f"{self.position.realized_return(self.price):+.2%})"
        )


@dataclass
class PositionBook:
    """Tracks open positions and fires exits when their plan says so."""

    memory: Any = None
    trailing: bool = False
    positions: dict[str, ManagedPosition] = field(default_factory=dict)
    closed: list[dict] = field(default_factory=list)

    # ---------- lifecycle ----------

    def open(
        self,
        *,
        symbol: str,
        asset_class: str,
        quantity: float,
        entry_price: float,
        plan: ExitPlan,
        thesis_id: Optional[str] = None,
        signal: Optional[str] = None,
        confidence: Optional[float] = None,
        venue: Optional[str] = None,
    ) -> ManagedPosition:
        """Register a fill. Adding to an existing position averages the entry
        and keeps the ORIGINAL plan — a stop should not drift looser because we
        bought more."""
        existing = self.positions.get(symbol)
        if existing is not None and existing.quantity > 0:
            total = existing.quantity + quantity
            avg = (
                existing.quantity * existing.entry_price + quantity * entry_price
            ) / total
            existing.quantity = total
            existing.entry_price = avg
            return existing

        position = ManagedPosition(
            symbol=symbol, asset_class=asset_class, quantity=quantity,
            entry_price=entry_price, plan=plan, thesis_id=thesis_id,
            signal=signal, confidence=confidence, venue=venue,
        )
        self.positions[symbol] = position
        return position

    def close(
        self, symbol: str, exit_price: float, *, reason: ExitReason = "manual"
    ) -> Optional[dict]:
        """Remove a position and record what the thesis actually did."""
        position = self.positions.pop(symbol, None)
        if position is None:
            return None

        realized = position.realized_return(exit_price)
        record = {
            "symbol": symbol,
            # Without this a closed trade cannot be attributed to a venue, and
            # every per-venue number on the dashboard would be the fund's total
            # printed twice.
            "asset_class": position.asset_class,
            "thesis_id": position.thesis_id,
            "reason": reason,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "quantity": position.quantity,
            "realized_return": realized,
            "realized_usd": position.quantity * (exit_price - position.entry_price)
            * (1 if position.plan.direction == "long" else -1),
            "held_seconds": time.time() - position.opened_at,
            # The plan is recorded with the outcome, not just the outcome. Stop
            # placement can only be judged against the stop that was set, and
            # after the position is popped there is nowhere else to read it.
            "stop": position.plan.stop,
            "target": position.plan.target,
            "atr": position.plan.atr,
            "venue": position.venue,
            "opened_at": position.opened_at,
            "closed_at": time.time(),
        }
        self.closed.append(record)
        self._persist_closed(record)
        self._record_outcome(position, realized, reason)
        return record

    # ---------- the tick ----------

    def check_exits(self, quotes: dict[str, float]) -> list[ExitSignal]:
        """Which open positions have hit their stop or target?

        `quotes` maps symbol → current price. A symbol with no quote is skipped
        rather than assumed flat: acting on a stale price is how a position
        gets closed at a number that never existed.

        Stop is checked BEFORE target. If a bar straddled both we cannot know
        which came first, so we assume the worse one — optimism here would
        quietly inflate every backtest and every scorecard.
        """
        signals: list[ExitSignal] = []
        for symbol, position in list(self.positions.items()):
            price = quotes.get(symbol)
            if price is None:
                continue
            if is_stop_breached(position.plan, price):
                signals.append(ExitSignal(position, "stop", price))
            elif is_target_reached(position.plan, price):
                signals.append(ExitSignal(position, "target", price))
            elif self.trailing:
                position.plan = trail_stop(position.plan, price)
        return signals

    def flatten_signals(
        self, quotes: dict[str, float], *, asset_class: Optional[str] = None
    ) -> list[ExitSignal]:
        """Exit everything (optionally one asset class). Used for the weekend
        crypto handoff."""
        out = []
        for symbol, position in list(self.positions.items()):
            if asset_class and position.asset_class != asset_class:
                continue
            price = quotes.get(symbol)
            if price is None:
                continue
            out.append(ExitSignal(position, "flatten", price))
        return out

    # ---------- reads ----------

    def get(self, symbol: str) -> Optional[ManagedPosition]:
        return self.positions.get(symbol)

    def open_symbols(self) -> list[str]:
        return list(self.positions)

    def unrealized_usd(self, quotes: dict[str, float]) -> float:
        total = 0.0
        for symbol, position in self.positions.items():
            price = quotes.get(symbol)
            if price is None:
                continue
            total += position.quantity * (price - position.entry_price) * (
                1 if position.plan.direction == "long" else -1
            )
        return total

    def status(self, quotes: Optional[dict[str, float]] = None) -> dict:
        quotes = quotes or {}
        return {
            "open": len(self.positions),
            "closed": len(self.closed),
            "unrealized_usd": round(self.unrealized_usd(quotes), 2),
            "positions": [
                {
                    "symbol": p.symbol,
                    "quantity": p.quantity,
                    "entry": p.entry_price,
                    "stop": p.plan.stop,
                    "target": p.plan.target,
                    "thesis_id": p.thesis_id,
                    "unrealized_pct": round(
                        p.unrealized_return(quotes[p.symbol]) * 100, 3
                    ) if p.symbol in quotes else None,
                }
                for p in self.positions.values()
            ],
        }

    # ---------- internals ----------

    def _persist_closed(self, record: dict) -> None:
        """Durability. `self.closed` is a list in a process that restarts."""
        if self.memory is None:
            return
        try:
            self.memory.record_closed_trade(record)
        except Exception:
            logger.exception("failed to persist the closed trade for %s",
                             record.get("symbol"))

    def _record_outcome(
        self, position: ManagedPosition, realized: float, reason: ExitReason
    ) -> None:
        """Write the thesis verdict so calibration has something to score.

        `signal` and `confidence` are what the committee staked; without them
        the row cannot be scored for calibration and the confidence shrink stays
        a guess forever.
        """
        if self.memory is None or not position.thesis_id:
            return
        try:
            self.memory.record_thesis_outcome(
                position.thesis_id,
                position.symbol,
                realized_return=realized,
                signal=position.signal,
                confidence=position.confidence,
                correct=realized > 0,
                notes=f"closed on {reason}",
            )
        except Exception:
            # Losing the scorecard entry must not fail the exit.
            logger.exception(
                "failed to record outcome for thesis %s", position.thesis_id
            )
