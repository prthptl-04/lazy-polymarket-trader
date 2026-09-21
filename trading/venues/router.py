"""Venue router — picks the venue and enforces the pre-trade gates.

This is the single chokepoint between a decision and a broker. Everything the
round table produces passes through `place()`, and `place()` refuses anything
that would be illegal, ill-timed, or account-endangering:

1. **Venue support** — the venue must handle that asset class.
2. **Session** — equities only when equities are open; extended-hours orders
   must be limit orders and must declare `extended_hours`.
3. **PDT** — under $25k, the order that would be the 4th day trade in 5
   business days is blocked outright (see trading/pdt.py).

What this deliberately does NOT do is decide *whether the trade is any good* —
that is the Outcome Grader's job, upstream. The router is about permission, not
merit. Both have to say yes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from trading.kill_switch import DailyLossKillSwitch
from trading.live_gate import LiveTradingGate
from trading.pdt import DayTradeTracker
from trading.sessions import Session, session_at
from trading.venues.base import (
    AssetClass,
    OrderAck,
    OrderRequest,
    Quote,
    VenueAdapter,
)
from trading.venues.retired import is_retired, retirement_reason


# Premarket books are thin. A wide spread there is how a "good" thesis turns
# into a bad fill, so the router refuses beyond this unless overridden.
MAX_EXTENDED_HOURS_SPREAD_BPS = 100


@dataclass(frozen=True)
class RouteDecision:
    allowed: bool
    reason: str
    venue_name: Optional[str] = None
    gate: Optional[str] = None      # venue | venue_retired | live_trading |
                                    # venue_session | mode_session | kill_switch |
                                    # session | pdt | spread


@dataclass
class VenueRouter:
    """Routes orders to venues under the pre-trade gates."""

    adapters: list[VenueAdapter] = field(default_factory=list)
    pdt: Optional[DayTradeTracker] = None
    kill_switch: Optional[DailyLossKillSwitch] = None
    live_gate: Optional[LiveTradingGate] = None
    # Per-venue trading sessions, toggled from the dashboard. Absent means
    # enabled: a venue you registered but never touched should work.
    enabled: dict[str, bool] = field(default_factory=dict)
    # Per-MODE sessions, keyed "<venue>:<paper|live>". The venue switch above is
    # the master; this says which side of the house may open positions. They are
    # separate because "stop paper while I watch a live position" and "stop this
    # venue entirely" are different instructions.
    modes: dict[str, bool] = field(default_factory=dict)
    max_extended_hours_spread_bps: int = MAX_EXTENDED_HOURS_SPREAD_BPS
    # Venue preference per asset class; first supporting adapter wins otherwise.
    preferences: dict[str, str] = field(default_factory=dict)
    # Which venue a symbol was actually opened at. A close MUST go back to the
    # broker that holds the position: routing a live exit to paper would mark a
    # position closed in our books while it is still open at the broker, which
    # is the single worst thing two execution venues can do to you.
    #
    # In memory only. After a restart the map is empty and an unknown close
    # falls back to the live venue when one is usable — a broker that rejects
    # "you don't hold this" is loud and harmless; the reverse is silent and not.
    opened_at: dict[str, str] = field(default_factory=dict)

    def register(self, adapter: VenueAdapter) -> None:
        self.adapters.append(adapter)

    # ---------- routing ----------

    # ---------- venue sessions ----------

    def is_enabled(self, name: str) -> bool:
        return self.enabled.get(name, True)

    def set_enabled(self, name: str, on: bool) -> bool:
        """Switch one venue's trading on or off. Returns the new state."""
        self.enabled[name] = bool(on)
        return self.enabled[name]

    @staticmethod
    def mode_of(adapter: VenueAdapter) -> str:
        """paper or live, asked of the adapter rather than assumed.

        Mirrors trading.live_gate: an adapter that does not declare itself paper
        is treated as live, because assuming a new venue is harmless is how real
        money moves by accident.
        """
        explicit = getattr(adapter, "is_live", None)
        if explicit is not None:
            return "live" if explicit else "paper"
        return "paper" if getattr(adapter, "name", "") == "paper" else "live"

    def is_mode_enabled(self, name: str, mode: str) -> bool:
        return self.modes.get(f"{name}:{mode}", True)

    def set_mode_enabled(self, name: str, mode: str, on: bool) -> bool:
        """Switch one venue's paper or live side. Returns the new state."""
        if mode not in ("paper", "live"):
            raise ValueError(f"mode must be paper or live, got {mode!r}")
        self.modes[f"{name}:{mode}"] = bool(on)
        return self.modes[f"{name}:{mode}"]

    def venue_for(
        self,
        asset_class: AssetClass,
        *,
        symbol: Optional[str] = None,
        is_close: bool = False,
    ) -> Optional[VenueAdapter]:
        """Pick the adapter for this order.

        Order of authority:

        1. **An explicit preference**, if it supports the class.
        2. **The venue that opened the position**, for a close. Nothing may
           override this — see `opened_at`.
        3. **Live, but only if it is both switched on AND permitted.** The
           live-mode session says the operator wants it; `LiveTradingGate` says
           rule #13 allows it. Either one saying no means paper, because a
           toggle must never be able to promote itself past the checklist.
        4. **Paper**, then whatever supports the class at all, so a refusal can
           still name a venue.
        """
        supporting = [a for a in self.adapters if a.supports(asset_class)]
        if not supporting:
            return None

        preferred = self.preferences.get(asset_class)
        if preferred:
            for a in supporting:
                if a.name == preferred:
                    return a

        if is_close and symbol:
            held = self.opened_at.get(symbol)
            if held:
                for a in supporting:
                    if a.name == held:
                        return a

        live = [a for a in supporting if self.mode_of(a) == "live"]
        usable_live = [a for a in live if self._live_is_permitted(a)]
        if usable_live and (not is_close or not symbol or symbol not in self.opened_at):
            return usable_live[0]

        for a in supporting:
            if self.mode_of(a) == "paper":
                return a
        return supporting[0]

    def _live_is_permitted(self, adapter: VenueAdapter) -> bool:
        """Switched on by the operator AND allowed by rule #13. Both, always."""
        if not self.is_enabled(adapter.name):
            return False
        if not self.is_mode_enabled(adapter.name, "live"):
            return False
        if self.live_gate is None:
            # No gate attached is not an endorsement. Refuse by default, same
            # stance the gate itself takes.
            return False
        try:
            return bool(self.live_gate.evaluate(adapter).allowed)
        except Exception:
            return False

    # ---------- gates ----------

    def evaluate(
        self,
        request: OrderRequest,
        moment: datetime,
        *,
        quote: Optional[Quote] = None,
    ) -> RouteDecision:
        """Dry-run every gate. `place()` calls this first; callers may too."""
        adapter = self.venue_for(request.asset_class, symbol=request.symbol,
                                 is_close=request.is_close)
        if adapter is None:
            return RouteDecision(
                allowed=False, gate="venue",
                reason=f"no registered venue supports {request.asset_class!r}",
            )

        # Retirement comes before everything, including rule #13: a venue the
        # fund no longer trades is not a venue with a strict checklist, it is
        # one with no path at all. Exits are still permitted, on the same
        # reasoning as the venue switch below — retiring a venue must not trap
        # the positions already open there.
        if is_retired(adapter.name) and not request.is_close:
            return RouteDecision(
                allowed=False, gate="venue_retired", venue_name=adapter.name,
                reason=(f"{adapter.name} is RETIRED and no longer trades. "
                        f"{retirement_reason(adapter.name)} Exits remain "
                        "allowed so open positions can be closed."),
            )

        # Rule #13 comes FIRST and applies to exits too. Every other gate is
        # about whether a trade is wise; this one is about whether real money
        # may move at all. An unmet checklist must not be bypassable by
        # labelling an order a "close".
        if self.live_gate is not None:
            verdict = self.live_gate.evaluate(adapter)
            if not verdict.allowed:
                return RouteDecision(
                    allowed=False, gate="live_trading", venue_name=adapter.name,
                    reason=verdict.reason,
                )

        # Venue session. Like the kill-switch, this NEVER blocks an exit —
        # switching a venue off must not trap the positions already open there.
        if not self.is_enabled(adapter.name) and not request.is_close:
            return RouteDecision(
                allowed=False, gate="venue_session", venue_name=adapter.name,
                reason=(f"{adapter.name} trading is switched OFF. Exits still "
                        "allowed; turn it on from the dashboard to open new positions."),
            )

        # The mode switch, same exemption as the venue switch: switching paper
        # off must not strand a paper position, and switching live off must not
        # strand a live one.
        mode = self.mode_of(adapter)
        if not self.is_mode_enabled(adapter.name, mode) and not request.is_close:
            return RouteDecision(
                allowed=False, gate="mode_session", venue_name=adapter.name,
                reason=(f"{mode} trading is switched OFF for {adapter.name}. Exits "
                        "still allowed; turn it on from the dashboard to open new "
                        "positions."),
            )

        # Daily loss limit comes first: once the fund is done for the day it is
        # done, regardless of how good the next setup looks. Closing orders
        # pass through — the switch must never trap us in a losing position.
        if self.kill_switch is not None:
            verdict = self.kill_switch.evaluate(moment, is_closing=request.is_close)
            if not verdict.allowed:
                return RouteDecision(
                    allowed=False, gate="kill_switch", venue_name=adapter.name,
                    reason=verdict.reason,
                )

        session = session_at(moment)

        if request.asset_class == "equity":
            if not session.equities_open:
                return RouteDecision(
                    allowed=False, gate="session", venue_name=adapter.name,
                    reason=(
                        f"equities are closed ({session.value}) — "
                        "crypto is the only venue right now"
                    ),
                )
            if session.is_extended_hours:
                if not request.extended_hours:
                    return RouteDecision(
                        allowed=False, gate="session", venue_name=adapter.name,
                        reason=(
                            f"{session.value} session requires extended_hours=True "
                            "on the order"
                        ),
                    )
                if request.order_type != "limit":
                    return RouteDecision(
                        allowed=False, gate="session", venue_name=adapter.name,
                        reason="extended hours requires a limit order",
                    )
                spread = quote.spread_bps if quote else None
                if spread is not None and spread > self.max_extended_hours_spread_bps:
                    return RouteDecision(
                        allowed=False, gate="spread", venue_name=adapter.name,
                        reason=(
                            f"extended-hours spread {spread} bps exceeds the "
                            f"{self.max_extended_hours_spread_bps} bps limit"
                        ),
                    )
            elif request.extended_hours:
                return RouteDecision(
                    allowed=False, gate="session", venue_name=adapter.name,
                    reason="extended_hours=True during the regular session",
                )

        # PDT binds only on closing an equity position.
        if self.pdt is not None and request.is_close:
            verdict = self.pdt.evaluate_close(
                request.symbol, moment,
                asset_class="crypto" if request.asset_class == "crypto" else "equity",
            )
            if not verdict.allowed:
                return RouteDecision(
                    allowed=False, gate="pdt", venue_name=adapter.name,
                    reason=verdict.reason,
                )

        return RouteDecision(
            allowed=True, venue_name=adapter.name,
            reason=f"routed to {adapter.name} ({session.value})",
        )

    # ---------- execution ----------

    async def place(
        self,
        request: OrderRequest,
        moment: datetime,
        *,
        quote: Optional[Quote] = None,
    ) -> OrderAck:
        """Gate, then submit. A refused gate never reaches the venue."""
        decision = self.evaluate(request, moment, quote=quote)
        if not decision.allowed:
            return OrderAck(
                accepted=False,
                client_order_id=request.client_order_id,
                status="rejected",
                error=f"[{decision.gate}] {decision.reason}",
                venue=decision.venue_name,
            )

        adapter = self.venue_for(request.asset_class, symbol=request.symbol,
                                 is_close=request.is_close)
        assert adapter is not None    # evaluate() already proved this
        ack = await adapter.place_order(request)

        # Remember where it lives, so the exit goes back to the same broker.
        # Deliberately NOT cleared on a close: a partial exit leaves the rest of
        # the position at that venue, and forgetting would send the remainder
        # wherever the mode rules happened to point. A stale entry is harmless —
        # reopening the symbol overwrites it.
        if ack.accepted and not request.is_close:
            self.opened_at[request.symbol] = adapter.name

        # Keep the day-trade ledger honest: only count orders that were TAKEN.
        # `is_filled`, not `accepted` — which is what every other consumer in
        # the codebase reads, and what this comment always meant. An accepted
        # limit that never traded used to record a phantom same-day open, and
        # `evaluate_close` would then classify a genuine close of a position
        # opened YESTERDAY as a day trade. At 3 used in the window that blocks
        # the exit outright, trapping a position the fund is trying to leave.
        if ack.is_filled and self.pdt is not None and request.asset_class == "equity":
            if request.is_close:
                self.pdt.record_close(request.symbol, moment, asset_class="equity")
            else:
                self.pdt.record_open(request.symbol, moment)
        return ack

    # ---------- reads ----------

    def status(self, moment: datetime) -> dict:
        session = session_at(moment)
        return {
            "session": session.value,
            "equities_open": session.equities_open,
            "extended_hours": session.is_extended_hours,
            "venues": [a.name for a in self.adapters],
            "sessions": {a.name: self.is_enabled(a.name) for a in self.adapters},
            "modes": {
                f"{a.name}:{self.mode_of(a)}": self.is_mode_enabled(a.name, self.mode_of(a))
                for a in self.adapters
            },
            "pdt": self.pdt.status(moment) if self.pdt else None,
            "kill_switch": self.kill_switch.status(moment) if self.kill_switch else None,
            "live_gate": self.live_gate.status() if self.live_gate else None,
            "live_permitted": {
                a.name: self._live_is_permitted(a)
                for a in self.adapters if self.mode_of(a) == "live"
            },
            "opened_at": dict(self.opened_at),
        }
