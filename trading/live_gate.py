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
        if self.memory is None:
            return 0
        try:
            return sum(1 for t in self.memory.recent_trades(limit=100_000)
                       if t.get("grade_pass"))
        except Exception:
            logger.exception("could not count graded paper trades")
            return 0

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
