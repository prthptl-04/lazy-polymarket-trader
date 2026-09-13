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
from trading.pdt import DayTradeTracker
from trading.sessions import Session, session_at
from trading.venues.base import (
    AssetClass,
    OrderAck,
    OrderRequest,
    Quote,
    VenueAdapter,
)


# Premarket books are thin. A wide spread there is how a "good" thesis turns
# into a bad fill, so the router refuses beyond this unless overridden.
MAX_EXTENDED_HOURS_SPREAD_BPS = 100


@dataclass(frozen=True)
class RouteDecision:
    allowed: bool
    reason: str
    venue_name: Optional[str] = None
    gate: Optional[str] = None      # which gate refused: venue | session | pdt | spread


@dataclass
class VenueRouter:
    """Routes orders to venues under the pre-trade gates."""

    adapters: list[VenueAdapter] = field(default_factory=list)
    pdt: Optional[DayTradeTracker] = None
    kill_switch: Optional[DailyLossKillSwitch] = None
    # Per-venue trading sessions, toggled from the dashboard. Absent means
    # enabled: a venue you registered but never touched should work.
    enabled: dict[str, bool] = field(default_factory=dict)
    max_extended_hours_spread_bps: int = MAX_EXTENDED_HOURS_SPREAD_BPS
    # Venue preference per asset class; first supporting adapter wins otherwise.
    preferences: dict[str, str] = field(default_factory=dict)

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

    def venue_for(self, asset_class: AssetClass) -> Optional[VenueAdapter]:
        preferred = self.preferences.get(asset_class)
        if preferred:
            for a in self.adapters:
                if a.name == preferred and a.supports(asset_class):
                    return a
        for a in self.adapters:
            if a.supports(asset_class):
                return a
        return None

    # ---------- gates ----------

    def evaluate(
        self,
        request: OrderRequest,
        moment: datetime,
        *,
        quote: Optional[Quote] = None,
    ) -> RouteDecision:
        """Dry-run every gate. `place()` calls this first; callers may too."""
        adapter = self.venue_for(request.asset_class)
        if adapter is None:
            return RouteDecision(
                allowed=False, gate="venue",
                reason=f"no registered venue supports {request.asset_class!r}",
            )

        # Venue session. Like the kill-switch, this NEVER blocks an exit —
        # switching a venue off must not trap the positions already open there.
        if not self.is_enabled(adapter.name) and not request.is_close:
            return RouteDecision(
                allowed=False, gate="venue_session", venue_name=adapter.name,
                reason=(f"{adapter.name} trading is switched OFF. Exits still "
                        "allowed; turn it on from the dashboard to open new positions."),
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

        adapter = self.venue_for(request.asset_class)
        assert adapter is not None    # evaluate() already proved this
        ack = await adapter.place_order(request)

        # Keep the day-trade ledger honest: only count orders that were taken.
        if ack.accepted and self.pdt is not None and request.asset_class == "equity":
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
            "pdt": self.pdt.status(moment) if self.pdt else None,
            "kill_switch": self.kill_switch.status(moment) if self.kill_switch else None,
        }
