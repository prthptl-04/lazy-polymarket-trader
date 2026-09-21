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
from typing import Any, Callable, Literal, Optional

from finance.exits import ExitPlan, is_stop_breached, is_target_reached, trail_stop

logger = logging.getLogger(__name__)


# "signal" is a DISCRETIONARY exit — the committee turned against a name it
# held. It is deliberately not "manual" (no human intervened) and not a
# barrier: `live_gate.graduation` filters `reason == "stop"` for stop
# discipline, and a discretionary cut is not evidence the risk system
# works. It is also the only exit class that puts a realised return
# BETWEEN the stop and the target, which is what lets the record disagree
# with the exit plan instead of restating it.
ExitReason = Literal["stop", "target", "flatten", "manual", "signal"]


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
    mode: Optional[str] = None
    # The mid the plan and the grade were built on. Kept beside the fill so the
    # cost of trading is a subtraction rather than a guess — and NOT stored as a
    # slippage figure: a derived column drifts from its inputs the first time
    # someone corrects one and not the other.
    planned_entry: Optional[float] = None
    entry_fill_source: Optional[str] = None
    spread_bps_at_entry: Optional[int] = None
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
    # Set by `build_fund`. Returns the whole fund blob — the book itself never
    # learns what a venue or a kill switch is.
    state_provider: Optional[Callable[[], dict]] = None
    trailing: bool = False
    positions: dict[str, ManagedPosition] = field(default_factory=dict)
    closed: list[dict] = field(default_factory=list)

    # ---------- lifecycle ----------

    # ---------- persistence ----------

    # A restored position must have opened in the past and within living
    # memory. There is no guard today, and a NULL or 0 `opened_at` yields a
    # `held_seconds` of ~55 years, which poisons every duration statistic
    # silently — a plausible-looking number in a column nobody re-reads.
    MAX_PLAUSIBLE_AGE_SECONDS = 365 * 24 * 3600.0

    def snapshot(self) -> list[dict]:
        """Every open position, flat.

        `opened_at` is stored EXACTLY. It is not a detail: `held_seconds` is
        `time.time() - opened_at`, so letting the dataclass default re-stamp it
        on restore yields time-since-restart — a plausible small number where a
        plausible large one belongs, biasing holding periods in the *same*
        direction as the bug this persistence exists to fix.

        The plan is stored at its CURRENT values, after any trailing ratchet.
        `r_multiples` divides by the planned risk, and restoring the original
        stop would widen a stop that had already tightened.

        Derived close-time fields (`exit_price`, `realized_*`, `held_seconds`)
        are deliberately absent — they are computed in `close()` and a second
        copy would drift.
        """
        return [
            {"symbol": p.symbol, "asset_class": p.asset_class,
             "quantity": p.quantity, "entry_price": p.entry_price,
             "stop": p.plan.stop, "target": p.plan.target, "atr": p.plan.atr,
             "plan_entry": p.plan.entry, "direction": p.plan.direction,
             "thesis_id": p.thesis_id, "signal": p.signal,
             "confidence": p.confidence, "venue": p.venue, "mode": p.mode,
             "planned_entry": p.planned_entry,
             "entry_fill_source": p.entry_fill_source,
             "spread_bps_at_entry": p.spread_bps_at_entry,
             "opened_at": p.opened_at}
            for p in self.positions.values()
        ]

    def restore(self, rows: Optional[list]) -> list[str]:
        """Rebuild the open book. Returns warnings; never raises.

        A row that cannot be trusted is QUARANTINED — left out of the book and
        named — rather than booked with a guess. A fabricated stop is worse
        than an unmanaged position, because the sizing arithmetic and the
        R-multiple both assume the stop is the one that was sized against.
        """
        warnings: list[str] = []
        now = time.time()
        for row in rows or []:
            try:
                symbol = str(row["symbol"])
                opened_at = float(row["opened_at"])
                if not (0 < opened_at <= now + 60):
                    warnings.append(
                        f"{symbol}: opened_at {opened_at!r} is not a time in the "
                        "recent past; quarantined rather than booked")
                    continue
                if now - opened_at > self.MAX_PLAUSIBLE_AGE_SECONDS:
                    warnings.append(
                        f"{symbol}: opened_at is over a year old; quarantined")
                    continue
                self.positions[symbol] = ManagedPosition(
                    symbol=symbol,
                    asset_class=str(row.get("asset_class") or "equity"),
                    quantity=float(row["quantity"]),
                    entry_price=float(row["entry_price"]),
                    plan=ExitPlan(
                        entry=float(row.get("plan_entry") or row["entry_price"]),
                        stop=float(row["stop"]), target=float(row["target"]),
                        direction=str(row.get("direction") or "long"),
                        atr=float(row.get("atr") or 0.0)),
                    thesis_id=row.get("thesis_id"),
                    signal=row.get("signal"),
                    confidence=row.get("confidence"),
                    venue=row.get("venue"),
                    # Unknown resolves to LIVE, matching live_gate and
                    # `_venue_mode`: a round trip nobody can attribute must not
                    # pad the bar that gates real money.
                    mode=row.get("mode") or "live",
                    planned_entry=row.get("planned_entry"),
                    # Absent resolves to "mid", never "venue": defaulting the
                    # other way injects a row into `_bridge` claiming $0.00 of
                    # trading cost, the exact fiction that bridge exposes.
                    entry_fill_source=row.get("entry_fill_source") or "mid",
                    spread_bps_at_entry=row.get("spread_bps_at_entry"),
                    opened_at=opened_at,
                )
            except (KeyError, TypeError, ValueError) as e:
                warnings.append(f"unreadable position row ({type(e).__name__}); skipped")
        return warnings

    def _save_state(self) -> None:
        """Persist after any change to the position SET.

        Called on open as well as close. Saving only on close would leave every
        position lost from its entry until its exit — which is the whole of the
        bug this exists to fix.
        """
        if self.state_provider is None or self.memory is None:
            return
        try:
            self.memory.put("fund", "runtime_state", self.state_provider())
        except Exception:
            logger.exception("could not persist fund state")

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
        mode: Optional[str] = None,
        planned_entry: Optional[float] = None,
        entry_fill_source: Optional[str] = None,
        spread_bps_at_entry: Optional[int] = None,
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
            signal=signal, confidence=confidence, venue=venue, mode=mode,
            planned_entry=planned_entry, entry_fill_source=entry_fill_source,
            spread_bps_at_entry=spread_bps_at_entry,
        )
        self.positions[symbol] = position
        self._save_state()
        return position

    def close(
        self, symbol: str, exit_price: float, *, reason: ExitReason = "manual",
        planned_exit: Optional[float] = None,
        exit_fill_source: Optional[str] = None,
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
            "mode": position.mode,
            "opened_at": position.opened_at,
            "closed_at": time.time(),
            "planned_entry": position.planned_entry,
            "planned_exit": planned_exit,
            "entry_fill_source": position.entry_fill_source,
            "exit_fill_source": exit_fill_source,
            "spread_bps_at_entry": position.spread_bps_at_entry,
        }
        self.closed.append(record)
        self._persist_closed(record)
        self._save_state()
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
