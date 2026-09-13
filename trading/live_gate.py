"""The live-trading gate for the fund path.

CLAUDE.md #4 and #13 were enforced by `trading/execution.py::Executor`, which
belonged to the Polymarket CLOB path and is NOT in the fund's path. The fund
goes ThesisPipeline -> VenueRouter -> adapter, so until now `PAPER_TRADING=true`
had no effect on it whatsoever: attaching a real venue would have placed real
orders with no gate at all.

This puts rule #13 back, in the path the fund actually uses. Every condition
must hold, checked per order, and any of them drifting back to unsafe
downgrades to a refusal rather than silently continuing:

1. `PAPER_TRADING=false` — explicit env intent.
2. The venue reports itself live AND authenticated.
3. Risk caps are live-appropriate (max_position_usd within the bankroll).
4. At least `MIN_PAPER_TRADES_FOR_LIVE` graded paper trades on record.
5. The operator recorded the explicit approval lesson.

A paper venue is always allowed: it cannot spend money, and blocking it would
stop the very paper trades condition 4 requires.

The gate refuses by DEFAULT. Every failure mode here — missing env, missing
memory, an exception while counting trades — lands on "paper only", because
the cost of wrongly refusing a live trade is a missed opportunity and the cost
of wrongly allowing one is real money.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from verification.criteria import (
    LIVE_APPROVAL_LESSON,
    MIN_PAPER_TRADES_FOR_LIVE,
    DEFAULT_CRITERIA,
    VerifiedOutcomeCriteria,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveVerdict:
    allowed: bool
    reason: str
    checks: dict

    @property
    def failed(self) -> list[str]:
        return [k for k, v in self.checks.items() if not v]


@dataclass
class LiveTradingGate:
    """Decides whether a real-money order may proceed."""

    memory: Any = None
    criteria: VerifiedOutcomeCriteria = DEFAULT_CRITERIA
    bankroll_usd: float = 0.0

    def evaluate(self, venue: Any) -> LiveVerdict:
        if not _is_live_venue(venue):
            return LiveVerdict(True, "paper venue — no real money at risk", {})

        checks = {
            "env_paper_trading_false": _env_live_intent(),
            "venue_authenticated": _venue_ready(venue),
            "risk_caps_live_appropriate": self._caps_ok(),
            "paper_trades_recorded": self._paper_trades_ok(),
            "operator_approval_lesson": self._approval_ok(),
        }
        if all(checks.values()):
            return LiveVerdict(True, "all five live-trading conditions met", checks)

        failed = [k for k, v in checks.items() if not v]
        return LiveVerdict(
            False,
            "LIVE TRADING BLOCKED — unmet: " + ", ".join(failed)
            + ". See CLAUDE.md #13.",
            checks,
        )

    def status(self) -> dict:
        """For the dashboard, so the operator can see the remaining steps."""
        checks = {
            "env_paper_trading_false": _env_live_intent(),
            "risk_caps_live_appropriate": self._caps_ok(),
            "paper_trades_recorded": self._paper_trades_ok(),
            "operator_approval_lesson": self._approval_ok(),
        }
        return {
            "live_possible": all(checks.values()),
            "checks": checks,
            "graded_paper_trades": self._graded_count(),
            "orders": self.order_diagnostics(),
            "graduation": self.graduation(),
            "required_paper_trades": MIN_PAPER_TRADES_FOR_LIVE,
        }

    # ---------- conditions ----------

    def _caps_ok(self) -> bool:
        """A cap larger than the bankroll is not a cap.

        Without a known bankroll we cannot judge, so this reads False — the
        gate refuses by default rather than assuming the caps are sane.
        """
        if self.bankroll_usd <= 0:
            return False
        return 0 < self.criteria.max_position_usd <= self.bankroll_usd

    def _graded_count(self) -> int:
        """Closed PAPER round trips — not orders.

        Rule #13's condition 4 says "50 paper TRADES". This used to count rows
        in `trade_log` with grade_pass, which includes orders that were accepted
        and never traded. Combined with extended-hours limit orders that cannot
        fill, the cheapest path to opening this gate was fifty orders in which
        nothing happened. A gate whose easiest route to open is the route where
        nothing occurred is not a gate.

        A closed trade can only exist downstream of `grade.passed` — the
        pipeline routes nothing ungraded — so "graded" is satisfied by
        construction. A NULL mode does NOT count: unknown resolves against the
        operator, the same stance as `_is_live_venue`.
        """
        if self.memory is None:
            return 0
        try:
            return sum(1 for t in self.memory.closed_trades(limit=100_000)
                       if t.get("mode") == "paper")
        except Exception:
            return 0

    def graduation(self) -> list[dict]:
        """The incubation checklist, beyond a trade count.

        DISPLAY ONLY. The five blocking conditions stay exactly the five rule
        #13 lists — adding blocking conditions the rule does not name is a
        CLAUDE.md edit, not a gate quietly tightening itself. This is what a
        desk would want to see before signing off real capital, with every item
        that cannot be measured saying so rather than rendering as a tick.
        """
        closed = []
        try:
            closed = [t for t in self.memory.closed_trades(limit=100_000)
                      if t.get("mode") == "paper"] if self.memory else []
        except Exception:
            closed = []
        diagnostics = self.order_diagnostics()
        stops = [t for t in closed if t.get("reason") == "stop"]
        within = [t for t in stops
                  if t.get("stop") and t.get("exit_price")
                  and abs(t["exit_price"] - t["stop"]) / t["stop"] <= 0.02]
        span_days = None
        stamps = [t.get("closed_at") for t in closed if t.get("closed_at")]
        if len(stamps) >= 2:
            span_days = (max(stamps) - min(stamps)) / 86_400

        def item(key, label, ok, detail):
            return {"id": key, "label": label, "ok": bool(ok), "detail": detail}

        return [
            item("round_trips", "50 filled closed paper round trips",
                 len(closed) >= MIN_PAPER_TRADES_FOR_LIVE,
                 f"{len(closed)} of {MIN_PAPER_TRADES_FOR_LIVE}"),
            item("span", "Spanning at least 3 months",
                 span_days is not None and span_days >= 90,
                 f"{span_days:.0f} days" if span_days else "not measurable: fewer than two closed trades"),
            item("regimes", "At least 2 volatility regimes", False,
                 "not measurable: regime labelling is not implemented, so this "
                 "box cannot be ticked automatically"),
            item("edge", "Net-of-cost edge above zero on a bad draw", False,
                 "not measurable until 30 closed trades carry venue fills"),
            item("stops_fired", "5 losses where the stop executed within tolerance",
                 len(within) >= 5,
                 f"{len(within)} of 5 — the risk system is untested until it has fired"),
            item("fill_rate", "Fill rate above 80%",
                 (diagnostics["fill_rate_pct"] or 0) >= 80,
                 f"{diagnostics['fill_rate_pct']}%" if diagnostics["fill_rate_pct"] is not None
                 else "not measurable: no graded orders yet"),
            item("divergence", "Trade ledgers agree", self._ledgers_agree(),
                 "closed trades with a thesis must equal resolved outcomes"),
            item("reconciled", "5 minimum-size real trades reconciled to paper", False,
                 "not measurable: no live trade has been placed — and this is the "
                 "step rule #13 does not have"),
        ]

    def _ledgers_agree(self) -> bool:
        try:
            closed = [t for t in self.memory.closed_trades(limit=100_000) if t.get("thesis_id")]
            outcomes = self.memory.resolved_outcomes(limit=100_000)
        except Exception:
            return False
        return len(closed) == len(outcomes)

    def order_diagnostics(self) -> dict:
        """Fill rate, reported beside the gate but never gating it.

        A near-zero extended-hours fill rate is how you discover the counter is
        being fed by orders that cannot trade.
        """
        try:
            rows = self.memory.recent_trades(limit=100_000) if self.memory else []
        except Exception:
            rows = []
        graded = [t for t in rows if t.get("grade_pass")]
        filled = [t for t in graded if t.get("filled")]
        by_session: dict[str, dict] = {}
        for t in graded:
            bucket = by_session.setdefault(t.get("session") or "unknown",
                                           {"graded": 0, "filled": 0})
            bucket["graded"] += 1
            bucket["filled"] += 1 if t.get("filled") else 0
        return {
            "orders_graded": len(graded),
            "orders_filled": len(filled),
            "fill_rate_pct": round(len(filled) / len(graded) * 100, 1) if graded else None,
            "fill_rate_by_session": by_session,
        }

    def _paper_trades_ok(self) -> bool:
        return self._graded_count() >= MIN_PAPER_TRADES_FOR_LIVE

    def _approval_ok(self) -> bool:
        if self.memory is None:
            return False
        try:
            lessons = self.memory.recent_lessons("*", limit=500)
        except Exception:
            return False
        return any(LIVE_APPROVAL_LESSON in str(l.get("lesson", "")).lower()
                   for l in lessons)


def _env_live_intent() -> bool:
    return os.environ.get("PAPER_TRADING", "true").strip().lower() == "false"


def _is_live_venue(venue: Any) -> bool:
    """Paper venues declare themselves. Anything that does not is treated as
    live — an unknown adapter must not be assumed harmless."""
    explicit = getattr(venue, "is_live", None)
    if explicit is not None:
        return bool(explicit)
    return getattr(venue, "name", "") != "paper"


def _venue_ready(venue: Any) -> bool:
    """Live venues must be able to prove they hold credentials."""
    for attr in ("can_trade", "authenticated"):
        value = getattr(venue, attr, None)
        if value is not None:
            return bool(value)
    session = getattr(venue, "session", None)
    if session is not None:
        summary = getattr(session, "auth_summary", None)
        if callable(summary):
            try:
                return bool(summary().get("authenticated"))
            except Exception:
                return False
    return False
