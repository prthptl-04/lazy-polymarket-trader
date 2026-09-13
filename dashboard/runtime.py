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
    data_provider: Optional[Any] = None

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
        """Open positions with their plan, so the UI can show where we exit."""
        book = self.position_book
        if book is None:
            return []
        rows = []
        for p in (getattr(book, "positions", {}) or {}).values():
            rows.append({
                "symbol": p.symbol,
                "asset_class": p.asset_class,
                "quantity": p.quantity,
                "entry": p.entry_price,
                "stop": p.plan.stop,
                "target": p.plan.target,
                "thesis_id": p.thesis_id,
                "opened_at": p.opened_at,
                "unrealized_pct": None,   # needs a live quote; the cycle marks it
            })
        return rows

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
        # Robinhood used to be unreachable from this process. It no longer is:
        # the daemon holds its own OAuth'd MCP session. This default only
        # applies when no adapter was registered.
        out.setdefault("robinhood", {
            "available": False,
            "reason": "not authenticated — run scripts_mcp_auth.py once",
        })
        return out

    async def feeds(self, venue: str, *, limit: int = 8) -> list[dict]:
        """Live quotes for what this venue is holding right now.

        Quotes come from the venue adapter, not from the data provider: the
        number that matters for an exit is the one the broker would fill at,
        and a provider's delayed mark would quietly disagree with it. A symbol
        whose quote fails is returned with its reason rather than dropped —
        a feed that silently shortens is indistinguishable from a flat book.
        """
        prediction = venue == "polymarket_us"
        held = [
            p for p in self.positions()
            if (p["asset_class"] == "prediction") == prediction
        ][:limit]

        adapter = (self.venues or {}).get(venue)
        rows: list[dict] = []
        for pos in held:
            row = {
                "symbol": pos["symbol"],
                "entry": pos["entry"],
                "bid": None, "ask": None, "last": None,
                "spread_bps": None, "change_pct": None, "reason": None,
            }
            if adapter is None:
                row["reason"] = "venue not attached"
            else:
                try:
                    q = await adapter.get_quote(pos["symbol"])
                    row.update(
                        bid=q.bid, ask=q.ask, last=q.last or q.mid,
                        spread_bps=q.spread_bps,
                    )
                    mark = row["last"] or q.mid
                    if mark and pos["entry"]:
                        row["change_pct"] = round(
                            (mark - pos["entry"]) / pos["entry"] * 100, 3
                        )
                except Exception as e:
                    row["reason"] = f"{type(e).__name__}"
            rows.append(row)
        return rows

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

    # ---------- overview ----------

    # ---------- venue sessions ----------

    SESSION_KEY = "venue_sessions"

    def venue_sessions(self) -> dict[str, bool]:
        """Which venues are currently allowed to OPEN positions."""
        router = self._router()
        if router is None:
            return {name: True for name in self.venues}
        names = {a.name for a in router.adapters} | set(self.venues)
        return {n: router.is_enabled(n) for n in sorted(names)}

    def set_venue_session(self, name: str, on: bool) -> dict:
        """Toggle one venue. Persisted, so a restart keeps the operator's intent
        rather than quietly re-enabling something they switched off."""
        router = self._router()
        if router is None:
            return {"ok": False, "reason": "no router attached", "sessions": {}}
        known = {a.name for a in router.adapters}
        if name not in known:
            return {"ok": False, "reason": f"unknown venue {name!r}",
                    "sessions": self.venue_sessions()}
        router.set_enabled(name, on)
        try:
            self.memory.put("dashboard", self.SESSION_KEY, router.enabled)
            self.memory.record_audit_event(
                "user", "venue_session_on" if on else "venue_session_off", name,
            )
        except Exception:
            pass
        return {"ok": True, "sessions": self.venue_sessions()}

    def restore_venue_sessions(self) -> None:
        router = self._router()
        if router is None:
            return
        try:
            saved = self.memory.get("dashboard", self.SESSION_KEY, {}) or {}
        except Exception:
            return
        for name, on in saved.items():
            router.set_enabled(name, bool(on))

    def _router(self):
        sched = self.fund_scheduler
        return getattr(getattr(sched, "fund", None), "router", None) if sched else None

    def record(self) -> dict:
        """Wins, losses and the equity curve — the 'am I making money' view.

        Built from closed positions, which is the only honest source: an open
        position has an opinion about itself, a closed one has a result.
        """
        closed = list(getattr(self.position_book, "closed", []) or [])
        wins = [c for c in closed if c.get("realized_usd", 0) > 0]
        losses = [c for c in closed if c.get("realized_usd", 0) < 0]
        realized = sum(c.get("realized_usd", 0.0) for c in closed)

        curve, running = [self.starting_bankroll_usd], self.starting_bankroll_usd
        for c in closed:
            running += c.get("realized_usd", 0.0)
            curve.append(round(running, 4))

        gross_win = sum(c["realized_usd"] for c in wins)
        gross_loss = abs(sum(c["realized_usd"] for c in losses))
        return {
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 2) if closed else None,
            "realized_usd": round(realized, 2),
            "best_usd": round(max((c["realized_usd"] for c in closed), default=0.0), 2),
            "worst_usd": round(min((c["realized_usd"] for c in closed), default=0.0), 2),
            # Profit factor beats win rate: a 70% win rate with tiny wins and
            # huge losses is a losing strategy that looks like a winning one.
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "equity_curve": curve,
        }

    def lessons(self, limit: int = 20) -> list[dict]:
        """What the fund has learned from its losses."""
        try:
            rows = self.memory.recent_lessons("*", limit=limit * 3)
        except Exception:
            return []
        out = []
        for r in rows:
            ctx = r.get("context") or {}
            if not isinstance(ctx, dict) or not ctx.get("code"):
                continue      # operational notes are not lessons from trading
            out.append({
                "code": ctx["code"], "symbol": ctx.get("symbol"),
                "severity": ctx.get("severity", "note"),
                "lesson": r.get("lesson", ""), "created": r.get("created"),
            })
        return out[:limit]

    def llm_status(self) -> dict:
        """Which model is answering, and how much Anthropic headroom is left."""
        router = getattr(self.fund_scheduler, "llm_router", None)
        if router is None:
            return {"attached": False}
        return {"attached": True, **router.status()}

    def paper_progress(self) -> dict:
        """Progress toward the rule-#13 bar, and what the record looks like.

        Separate from `record()` because the question is different: record()
        asks "am I making money", this asks "has the fund earned the right to
        trade real money yet, and is it getting better while it waits".
        """
        from verification.criteria import MIN_PAPER_TRADES_FOR_LIVE
        try:
            trades = self.memory.recent_trades(limit=100_000)
        except Exception:
            trades = []
        graded = [t for t in trades if t.get("grade_pass")]

        card = self.scorecard()
        record = self.record()
        lessons = self.lessons(limit=200)
        seats = card.get("seats") or []
        # A seat is "improving" once it is both calibrated and better than a
        # coin flip — beating chance while claiming 95% certainty is not skill.
        improving = [s for s in seats if s.get("calibrated") and s.get("beats_coin_flip")]

        return {
            "graded_paper_trades": len(graded),
            "required": MIN_PAPER_TRADES_FOR_LIVE,
            "pct_complete": round(min(100.0, len(graded) / MIN_PAPER_TRADES_FOR_LIVE * 100), 1),
            "total_trades_logged": len(trades),
            "deliberations": len(self.deliberations(limit=500)),
            "resolved": card.get("resolved", 0),
            "committee": card.get("committee"),
            "seats": seats,
            "seats_calibrated": len(improving),
            "seats_scored": len([s for s in seats if s.get("samples")]),
            "lessons_learned": len(lessons),
            "shrink_fit": card.get("fit"),
            # The paper engine IS the position book today, so its curve is
            # the record's curve. Exposed here so the paper panel does not
            # have to fetch the live record to draw its own performance.
            "equity_curve": record["equity_curve"],
            "realized_usd": record["realized_usd"],
            "win_rate": record["win_rate"],
            "closed": record["closed"],
        }

    # ---------- trade history with agent attribution ----------

    def trade_history(self, limit: int = 25) -> list[dict]:
        """Closed trades joined to the deliberation that caused them.

        The attribution is the point. A row says which seats backed the losing
        call and which one dissented and was right, because "the committee was
        wrong" is not actionable and "the Quant was confidently wrong while the
        Risk Manager objected" is.

        Blame is assigned ONLY on a loss, and only to seats whose own signal
        matched the consensus — a seat that abstained or dissented is not
        responsible for a call it did not make.
        """
        try:
            outcomes = self.memory.resolved_outcomes(limit=limit * 2)
            lessons = self.memory.recent_lessons("*", limit=500)
        except Exception:
            return []

        by_symbol: dict[str, list[dict]] = {}
        for l in lessons:
            ctx = l.get("context") or {}
            if isinstance(ctx, dict) and ctx.get("symbol"):
                by_symbol.setdefault(ctx["symbol"], []).append(
                    {"code": ctx.get("code"), "lesson": l.get("lesson", "")})

        rows: list[dict] = []
        for o in outcomes[:limit]:
            thesis = None
            try:
                thesis = self.memory.get_deliberation(o["thesis_id"])
            except Exception:
                pass
            payload = (thesis or {}).get("payload") or {}
            opinions = payload.get("opinions") or []
            consensus = payload.get("consensus") or {}
            signal = consensus.get("signal") or o.get("signal")
            realized = o.get("realized_return")
            won = bool(o.get("correct"))

            backed, dissented, abstained = [], [], []
            for op in opinions:
                if op.get("failed"):
                    abstained.append(op.get("seat_name"))
                elif op.get("signal") == signal:
                    backed.append({"name": op.get("seat_name"),
                                   "confidence": op.get("confidence"),
                                   "reasoning": op.get("reasoning", "")})
                elif op.get("signal") in ("bullish", "bearish"):
                    dissented.append({"name": op.get("seat_name"),
                                      "signal": op.get("signal"),
                                      "reasoning": op.get("reasoning", "")})

            rows.append({
                "thesis_id": o["thesis_id"],
                "symbol": o["symbol"],
                "resolved_at": o.get("resolved_at"),
                "side": "buy" if signal == "bullish" else "sell" if signal == "bearish" else "—",
                "signal": signal,
                "confidence": consensus.get("confidence") or o.get("confidence"),
                "realized_return": realized,
                "realized_pct": None if realized is None else round(realized * 100, 2),
                "won": won,
                "notes": o.get("notes"),
                # Only meaningful on a loss; a seat that was right is not to blame.
                "blamed": [] if won else backed,
                "vindicated": dissented if not won else [],
                "abstained": abstained,
                "actions": by_symbol.get(o["symbol"], [])[:4],
            })
        return rows

    def latest_deliberation(self) -> Optional[dict]:
        """Newest debate with every seat's reasoning, for the dialogue panel."""
        try:
            rows = self.memory.recent_deliberations(limit=1)
        except Exception:
            return None
        if not rows:
            return None
        return self.deliberation(rows[0]["thesis_id"])

    def agents(self) -> list[dict]:
        """The roster, for the UI's hover cards."""
        from roundtable.seats import ALL_SEATS
        icons = {
            "analyst": "\U0001F4D8", "sentiment": "\U0001F4AC", "quant": "\U0001F4C8",
            "risk": "\U0001F6E1", "corroborator": "\U0001F50D", "devils_advocate": "\U0001F608",
        }
        out = [
            {"id": s.id, "name": s.name, "mandate": s.mandate,
             "round": s.round, "icon": icons.get(s.id, "\U0001F464")}
            for s in ALL_SEATS
        ]
        out.append({"id": "chair", "name": "Chair", "round": 3,
                    "icon": "\U0001F3DB",
                    "mandate": "Synthesises the seats; weights argument quality over vote count"})
        return out

    async def candles(self, symbol: str, *, lookback: int = 60) -> dict:
        """Price series for a chart. Empty rather than fabricated when the
        provider has nothing — a made-up line on a trading dashboard is worse
        than a blank panel."""
        provider = getattr(self, "data_provider", None)
        if provider is None:
            return {"symbol": symbol, "closes": [], "reason": "no data provider attached"}
        try:
            history = await provider.get_history(symbol, lookback=lookback)
        except Exception as e:
            return {"symbol": symbol, "closes": [], "reason": f"{type(e).__name__}"}
        if history is None:
            return {"symbol": symbol, "closes": [], "reason": "no history for this symbol"}

        position = (getattr(self.position_book, "positions", {}) or {}).get(symbol)
        return {
            "symbol": symbol,
            "closes": list(history.closes),
            "highs": [b.high for b in history.bars],
            "lows": [b.low for b in history.bars],
            # Overlaid on the chart so the plan is visible, not just the price.
            "entry": position.entry_price if position else None,
            "stop": position.plan.stop if position else None,
            "target": position.plan.target if position else None,
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
