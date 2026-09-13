"""Shared runtime state for the dashboard.

Fund-only as of the CLOB removal. Everything the Polymarket on-chain path
needed — OrderBookCache, PositionTracker, OrderManager, CashoutEngine,
AutonomousLoop — is gone; `FundScheduler` replaces it, and venues are reached
through `VenueAdapter`.

Read-only except for GO/STOP, which is delegated to the scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from dashboard.ws_hub import WebSocketHub
from finance.pnl import equity_curve_from_trades
from finance.risk_metrics import max_drawdown, sharpe_ratio
from memory.store import MemoryStore


@dataclass
class DashboardRuntime:
    memory: MemoryStore
    hub: WebSocketHub = field(default_factory=WebSocketHub)
    starting_bankroll_usd: float = 100.0
    fund_scheduler: Optional[Any] = None
    position_book: Optional[Any] = None
    # name -> VenueAdapter, for the header balance strip.
    venues: dict[str, Any] = field(default_factory=dict)

    # ---------- status ----------

    def status(self) -> dict:
        if self.fund_scheduler is None:
            return {"state": "stopped", "attached": False}
        s = self.fund_scheduler.status()
        return {**s, "attached": True}

    def fund_status(self) -> dict:
        if self.fund_scheduler is None:
            return {"attached": False}
        return {"attached": True, **self.fund_scheduler.status()}

    # ---------- money ----------

    def pnl(self) -> dict:
        """Book value from the position book, marked where quotes are known."""
        realized = sum(c.get("realized_usd", 0.0) for c in
                       getattr(self.position_book, "closed", []) or [])
        open_n = len(getattr(self.position_book, "positions", {}) or {})
        return {
            "starting_bankroll_usd": self.starting_bankroll_usd,
            "realized_usd": round(realized, 4),
            "unrealized_usd": 0.0,   # needs live quotes; the fund cycle marks it
            "equity_usd": round(self.starting_bankroll_usd + realized, 4),
            "open_positions": open_n,
        }

    def positions(self) -> list[dict]:
        book = self.position_book
        if book is None:
            return []
        return book.status().get("positions", [])

    def open_orders(self) -> list[dict]:
        return []      # orders live at the venue now; no local mirror

    async def balances(self) -> dict:
        """Per-venue cash for the header.

        Robinhood is reachable only over MCP, which is bound to a Claude Code
        session — this process cannot call it. Rather than show a stale or
        invented number, it reports `available: False` with the reason.
        """
        out: dict[str, dict] = {}
        for name, venue in (self.venues or {}).items():
            try:
                acct = await venue.account()
                out[name] = {
                    "available": True,
                    "cash_usd": round(acct.cash_usd, 4),
                    "equity_usd": round(acct.equity_usd, 4),
                }
            except Exception as e:
                out[name] = {"available": False, "reason": f"{type(e).__name__}"}
        out.setdefault("robinhood", {
            "available": False,
            "reason": "MCP-only — not reachable from this process",
        })
        return out

    # ---------- history ----------

    def recent_trades(self, limit: int = 20) -> list[dict]:
        return self.memory.recent_trades(limit=limit)

    def recent_audit(self, limit: int = 50) -> list[dict]:
        return self.memory.recent_audit_events(limit=limit)

    def risk_metrics(self) -> dict:
        trades = self.memory.recent_trades(limit=10_000)
        curve = equity_curve_from_trades(trades, outcomes={},
                                         starting_bankroll=self.starting_bankroll_usd)
        returns = [
            (curve[i] - curve[i - 1]) / max(1e-9, curve[i - 1])
            for i in range(1, len(curve))
        ]
        return {
            "max_drawdown_pct": round(max_drawdown(curve) * 100, 3),
            "sharpe": round(sharpe_ratio(returns), 3),
            "trade_count": len(trades),
        }

    # ---------- round table ----------

    def deliberations(self, limit: int = 25) -> list[dict]:
        try:
            rows = self.memory.recent_deliberations(limit=limit)
        except Exception:
            return []
        return [
            {
                "thesis_id": r["thesis_id"], "symbol": r["symbol"],
                "asset_class": r["asset_class"], "status": r["status"],
                "signal": r["signal"], "confidence": r["confidence"],
                "created": r["created"],
                "tally": (r.get("payload") or {}).get("tally", {}),
                "seats": len((r.get("payload") or {}).get("opinions", [])),
            }
            for r in rows
        ]

    def deliberation(self, thesis_id: str) -> Optional[dict]:
        try:
            row = self.memory.get_deliberation(thesis_id)
        except Exception:
            return None
        if row is None:
            return None
        payload = row.get("payload") or {}
        opinions = payload.get("opinions", [])
        return {
            "thesis_id": row["thesis_id"], "symbol": row["symbol"],
            "asset_class": row["asset_class"], "status": row["status"],
            "created": row["created"], "updated": row["updated"],
            "opinions": opinions,
            "consensus": payload.get("consensus") or {},
            "tally": payload.get("tally", {}),
            "unanimous": _is_unanimous(payload.get("tally", {})),
            "abstentions": [o["seat_name"] for o in opinions if o.get("failed")],
        }

    def scorecard(self) -> dict:
        from roundtable.calibration import fit_confidence_shrink, score_seats
        try:
            delibs = self.memory.recent_deliberations(limit=500)
            outcomes = self.memory.resolved_outcomes(limit=500)
        except Exception:
            return {"resolved": 0, "committee": None, "seats": [], "fit": None}
        card = score_seats(delibs, {o["thesis_id"]: o for o in outcomes})
        fit = fit_confidence_shrink(outcomes)
        return {
            **card.as_dict(),
            "fit": {
                "shrink": fit.shrink, "samples": fit.samples,
                "realized_hit_rate": fit.realized_hit_rate,
                "usable": fit.usable, "reason": fit.reason,
            },
        }


def build_runtime(
    *,
    memory: Optional[MemoryStore] = None,
    starting_bankroll_usd: float = 100.0,
    venues: Optional[dict[str, Any]] = None,
) -> DashboardRuntime:
    return DashboardRuntime(
        memory=memory or MemoryStore(),
        starting_bankroll_usd=starting_bankroll_usd,
        venues=venues or {},
    )


def _is_unanimous(tally: dict) -> bool:
    """One voice agreeing with itself is not a consensus — needs 2+ seats."""
    counts = [n for n in (tally or {}).values() if n]
    return len(counts) == 1 and sum(counts) >= 2
