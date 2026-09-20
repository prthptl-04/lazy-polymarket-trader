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
from finance.risk_metrics import max_drawdown
from memory.store import MemoryStore
from trading.venues.retired import is_retired, retirement_reason


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
        """Drawdown over the REALISED equity curve.

        This used to build its curve from `trade_log` with `outcomes={}` — and
        `finance.pnl` additionally skips every `paper=True` row — so both figures
        were identically 0.0 forever, printed as measurements. A 0.0% drawdown
        reads as "never lost", which is the most flattering lie this dashboard
        could tell.

        Sharpe is gone rather than fixed. Annualising trade-indexed returns at
        252 periods assumes a period is a day; here a period is a closed trade
        of arbitrary length, so the number was meaningless independently of the
        empty input.
        """
        closed = self._closed_trades()
        curve, running = [self.starting_bankroll_usd], self.starting_bankroll_usd
        for c in closed:
            running += c.get("realized_usd", 0.0)
            curve.append(round(running, 4))
        return {
            "max_drawdown_pct": round(max_drawdown(curve) * 100, 3) if closed else None,
            "trade_count": len(closed),
            # Says why the number is absent instead of showing a zero that looks
            # like an answer.
            "reason": None if closed else "no closed trades yet",
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
    MODE_KEY = "venue_mode_sessions"

    def venue_sessions(self) -> dict[str, bool]:
        """Which venues are currently allowed to OPEN positions."""
        router = self._router()
        if router is None:
            return {name: True for name in self.venues}
        names = {a.name for a in router.adapters} | set(self.venues)
        return {n: router.is_enabled(n) for n in sorted(names)}

    def venue_modes(self) -> dict[str, dict]:
        """Per-venue paper/live switches, and whether each side can act at all.

        `attached` says whether an adapter of that mode is even registered —
        without it, a live GO would be a button that promises something the
        process cannot do. The dashboard shows the reason instead.
        """
        router = self._router()
        names = sorted({a.name for a in (router.adapters if router else [])} | set(self.venues))
        attached: dict[str, set[str]] = {}
        if router is not None:
            for a in router.adapters:
                attached.setdefault(a.name, set()).add(router.mode_of(a))
        out: dict[str, dict] = {}
        for n in names:
            out[n] = {
                mode: {
                    "on": router.is_mode_enabled(n, mode) if router else True,
                    "attached": mode in attached.get(n, set()),
                }
                for mode in ("paper", "live")
            }
        return out

    def set_venue_mode(self, name: str, mode: str, on: bool) -> dict:
        """Toggle one venue's paper or live side. Persisted like the master."""
        router = self._router()
        if router is None:
            return {"ok": False, "reason": "no router attached", "modes": {}}
        if mode not in ("paper", "live"):
            return {"ok": False, "reason": f"unknown mode {mode!r}",
                    "modes": self.venue_modes()}
        if name not in {a.name for a in router.adapters}:
            return {"ok": False, "reason": f"unknown venue {name!r}",
                    "modes": self.venue_modes()}
        router.set_mode_enabled(name, mode, on)
        try:
            self.memory.put("dashboard", self.MODE_KEY, router.modes)
            self.memory.record_audit_event(
                "user", f"{mode}_session_{'on' if on else 'off'}", name,
            )
        except Exception:
            pass
        return {"ok": True, "modes": self.venue_modes()}

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
        try:
            saved_modes = self.memory.get("dashboard", self.MODE_KEY, {}) or {}
        except Exception:
            return
        for key, on in saved_modes.items():
            name, _, mode = key.rpartition(":")
            if name and mode in ("paper", "live"):
                router.set_mode_enabled(name, mode, bool(on))

    def _router(self):
        sched = self.fund_scheduler
        return getattr(getattr(sched, "fund", None), "router", None) if sched else None

    # Kept so CLOSED prediction trades still attribute to the venue that made
    # them. Polymarket is retired (trading/venues/retired.py), so nothing new
    # can land here — but deleting the mapping would orphan the history rather
    # than retire the venue.
    VENUE_OF_ASSET = {"prediction": "polymarket_us"}

    # Below this a win/loss ratio describes the exit geometry rather than skill.
    MIN_TRADES_FOR_RATIOS = 50

    def record(self, venue: Optional[str] = None, *,
               asset_class: Optional[str] = None) -> dict:
        """Wins, losses and the equity curve — the 'am I making money' view.

        Built from closed positions, which is the only honest source: an open
        position has an opinion about itself, a closed one has a result.

        `venue` narrows it to one book. Trades closed before asset class was
        recorded have no venue and are counted only in the fund-wide view —
        silently filing them under the broker would invent a history.

        `asset_class` narrows it further, and is the slice that actually means
        something now the fund trades one venue: equities run Monday to Friday
        and crypto runs at weekends, so they are two different strategies on
        two different calendars sharing one account. A single blended Robinhood
        number hides which of the two is working.
        """
        closed = self._closed_trades()
        if venue is not None:
            closed = [
                c for c in closed
                if c.get("asset_class")
                and self.VENUE_OF_ASSET.get(c["asset_class"], "robinhood") == venue
            ]
        if asset_class is not None:
            closed = [c for c in closed if c.get("asset_class") == asset_class]
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
            # Gated. On a 2xATR stop / 3xATR target geometry, 3W/2L returns
            # PF ~ 2.25 mechanically, with zero skill — and a reader takes 2.25
            # for an edge. The same record() feeds the Overview, so the guard
            # lives here rather than in one view.
            "profit_factor": (
                round(gross_win / gross_loss, 2)
                if gross_loss and len(closed) >= self.MIN_TRADES_FOR_RATIOS else None
            ),
            "profit_factor_reason": (
                None if len(closed) >= self.MIN_TRADES_FOR_RATIOS
                else f"{len(closed)} of {self.MIN_TRADES_FOR_RATIOS} trades — a ratio this "
                     "early reports the exit geometry, not an edge"
            ),
            "concentration": self._concentration(closed, realized),
            "bridge": self._bridge(closed),
            "equity_curve": curve,
        }

    @staticmethod
    def _concentration(closed: list[dict], realized: float) -> dict:
        """How much of the record is one trade, and how many ran at once."""
        if not closed:
            return {"top1_symbol": None, "top1_share_pct": None,
                    "max_concurrent": 0, "reason": "no closed trades yet"}
        best = max(closed, key=lambda c: c.get("realized_usd", 0.0))
        events: list[tuple[float, int]] = []
        for c in closed:
            opened, shut = c.get("opened_at"), c.get("closed_at")
            if opened is None or shut is None:
                continue
            events.append((opened, 1))
            events.append((shut, -1))
        concurrent = running = 0
        for _, delta in sorted(events):
            running += delta
            concurrent = max(concurrent, running)
        return {
            "top1_symbol": best.get("symbol"),
            # Undefined against a non-positive book: "top trade: 420% of P&L" is
            # arithmetically true and gets the whole panel discarded.
            "top1_share_pct": (
                round(best.get("realized_usd", 0.0) / realized * 100, 1)
                if realized > 0 else None
            ),
            "max_concurrent": concurrent,
            "reason": None if realized > 0
                      else "net P&L is not positive — there is no share to attribute",
        }

    @staticmethod
    def _bridge(closed: list[dict]) -> dict:
        """Gross to net: what the mid-to-mid record would have said, what the
        fills actually cost, and what is left.

        Only rows carrying a VENUE price on both legs enter it. A mid-priced row
        has planned == fill by construction, so including it contributes a
        perfectly plausible $0.00 of cost and drags the average toward "trading
        is free" — the exact fiction this bridge exists to expose.
        """
        priced = [c for c in closed
                  if c.get("entry_fill_source") == "venue"
                  and c.get("exit_fill_source") == "venue"
                  and c.get("planned_entry") and c.get("planned_exit")]
        if not priced:
            return {"gross_usd": None, "cost_usd": None, "net_usd": None,
                    "trades_priced": 0, "trades_unpriced": len(closed),
                    "reason": "no closed trade carries a venue fill price yet"}
        gross = sum(c["quantity"] * (c["planned_exit"] - c["planned_entry"]) for c in priced)
        net = sum(c.get("realized_usd", 0.0) for c in priced)
        return {
            "gross_usd": round(gross, 4), "cost_usd": round(gross - net, 4),
            "net_usd": round(net, 4), "trades_priced": len(priced),
            "trades_unpriced": len(closed) - len(priced), "reason": None,
        }

    def _closed_trades(self) -> list[dict]:
        """The record, from the durable ledger.

        Preferring the store over `position_book.closed` is what makes the paper
        record survive a restart — the in-memory list is empty on every boot,
        and a fund that forgets last week's losses on a redeploy is a fund
        grading itself on a fresh start it did not earn.

        The in-memory list is the fallback for a runtime with no store attached
        (tests, mostly), and for the window before the first close is written.
        """
        rows: list[dict] = []
        try:
            rows = self.memory.closed_trades(limit=10_000)
        except Exception:
            rows = []
        if rows:
            return rows
        return list(getattr(self.position_book, "closed", []) or [])

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

    def _live_gate(self):
        """The gate the fund actually routes through, or a read-only stand-in.

        Preferring the router's own gate means the page reports what the
        executor will enforce, not a second opinion about it.
        """
        router = self._router()
        gate = getattr(router, "live_gate", None) if router is not None else None
        if gate is not None:
            return gate
        from trading.live_gate import LiveTradingGate
        return LiveTradingGate(memory=self.memory,
                               bankroll_usd=self.starting_bankroll_usd)

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
        # Graded ORDERS. Worth showing — sixty orders that produced no round
        # trip is a real fact — but it is not the bar, and calling it by the
        # bar's name is what let the page read "ready for live" on orders that
        # never traded.
        graded_orders = [t for t in trades if t.get("grade_pass")]
        # The bar itself comes from the gate that enforces it, never from a
        # second count that can drift away from it.
        graded = self._live_gate().graded_paper_trades()

        card = self.scorecard()
        record = self.record()
        lessons = self.lessons(limit=200)
        seats = card.get("seats") or []
        # A seat is "improving" once it is both calibrated and better than a
        # coin flip — beating chance while claiming 95% certainty is not skill.
        improving = [s for s in seats if s.get("calibrated") and s.get("beats_coin_flip")]

        return {
            "graded_paper_trades": graded,
            "required": MIN_PAPER_TRADES_FOR_LIVE,
            "pct_complete": round(min(100.0, graded / MIN_PAPER_TRADES_FOR_LIVE * 100), 1),
            "graded_orders": len(graded_orders),
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

    def roundtable_thread(self, limit: int = 6) -> list[dict]:
        """The last few debates flattened into one conversation, oldest first.

        `latest_deliberation` answers "what is the committee saying about this
        candidate"; this answers "what has the committee been saying", which is
        the question a running thread is for. Chair messages are interleaved at
        the end of their own debate so the transcript reads in the order the
        room actually spoke.
        """
        rows = []
        try:
            recent = self.memory.recent_deliberations(limit=limit)
        except Exception:
            return []
        for row in reversed(recent):            # oldest first: a thread grows down
            payload = row.get("payload") or {}
            created = row.get("created")
            for op in payload.get("opinions", []):
                rows.append({
                    "thesis_id": row["thesis_id"], "symbol": row["symbol"],
                    "created": created,
                    "seat_id": op.get("seat_id"), "seat_name": op.get("seat_name"),
                    "signal": op.get("signal"), "confidence": op.get("confidence"),
                    "reasoning": op.get("reasoning", ""),
                    "concerns": op.get("concerns") or [],
                    "failed": bool(op.get("failed")), "error": op.get("error"),
                    "role": "seat",
                })
            consensus = payload.get("consensus") or {}
            if consensus.get("summary"):
                rows.append({
                    "thesis_id": row["thesis_id"], "symbol": row["symbol"],
                    "created": created,
                    "seat_id": "chair", "seat_name": "Chair",
                    "signal": consensus.get("signal"),
                    "confidence": consensus.get("confidence"),
                    "reasoning": consensus["summary"],
                    "concerns": [consensus["dissent"]] if consensus.get("dissent") else [],
                    "failed": False, "error": None,
                    "role": "chair",
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

    # ---------- is it luck? ----------

    def edge(self) -> dict:
        """Expectancy in R, and whether it can be told from chance.

        Below MIN_SAMPLES_FOR_EDGE the scalars are null and the raw R values are
        returned instead: a list of five numbers is honest, a mean of five
        numbers with a t-statistic beside it is not. The binomial p-value is
        reported at any n because it is the test that does not need a big one.
        """
        from finance.risk_metrics import (
            MIN_SAMPLES_FOR_EDGE, binomial_p_value, bootstrap_mean_p5,
            r_multiples, samples_for_significance, t_statistic,
        )
        import statistics

        values, excluded = r_multiples(self._closed_trades())
        n = len(values)
        wins = sum(1 for v in values if v > 0)
        mean = statistics.fmean(values) if values else None
        # Break-even hit rate for the realised payoff ratio: a 1.5R book only
        # needs 40%, so 55% is not the achievement it reads as.
        avg_win = statistics.fmean([v for v in values if v > 0]) if wins else None
        avg_loss = abs(statistics.fmean([v for v in values if v <= 0])) if n - wins else None
        p0 = (avg_loss / (avg_win + avg_loss)) if avg_win and avg_loss else None

        enough = n >= MIN_SAMPLES_FOR_EDGE
        return {
            "n": n,
            "excluded": excluded,
            "r_values": [round(v, 3) for v in values],
            "mean_r": round(mean, 3) if enough and mean is not None else None,
            "sd_r": round(statistics.stdev(values), 3) if n >= 2 else None,
            "t_stat": round(t_statistic(values), 3) if enough and t_statistic(values) else None,
            "bootstrap_p5_mean_r": (
                round(bootstrap_mean_p5(values), 3) if bootstrap_mean_p5(values) is not None else None
            ),
            "binomial_p": (
                round(binomial_p_value(wins, n, p0), 4) if p0 and n else None
            ),
            "p0": round(p0, 3) if p0 else None,
            "wins": wins,
            "n_for_significance": samples_for_significance(values),
            "verdict": (
                "no closed trades yet" if n == 0
                else f"n too small to distinguish from luck — {n} of {MIN_SAMPLES_FOR_EDGE}"
                if not enough else "estimable"
            ),
            "note": (
                "50 closed trades is the operational bar in rule #13, not a "
                "statistical one. Significance depends on dispersion, not on a "
                "round number."
            ),
        }

    def seat_agreement(self) -> dict:
        """Are the seats independent, or one opinion with six voices?"""
        from roundtable.agreement import pairwise_agreement
        try:
            rows = self.memory.recent_deliberations(limit=500)
        except Exception:
            return {"pairs": [], "n_deliberations": 0, "mean_kappa": None,
                    "reason": "deliberations could not be read"}
        return pairwise_agreement(rows)

    # ---------- per-venue statistics ----------

    async def venue_stats(self, venue: str, *,
                          asset_class: Optional[str] = None) -> dict:
        """Two records for one venue, each labelled.

        `fund` is what THIS FUND did at that venue, from the position book.
        `broker` is what the ACCOUNT did, from the venue's own ledger. They
        answer different questions and are never merged: the account's history
        includes trades the fund never made, and the fund's paper record
        includes fills the broker never saw.

        `primary` names the one the panel should headline — the broker when it
        is reachable, because a panel labelled with a venue's name should show
        that venue's money.

        `asset_class` slices the FUND record only. The broker's ledger is not
        sliceable by asset class, so asking for one suppresses it rather than
        showing an account-wide number under an "equities" heading — which
        would read as the equity strategy's P&L and be wrong by every crypto
        trade in the account.
        """
        fund = self.record(venue, asset_class=asset_class)
        adapter = next(
            (a for a in (self._router().adapters if self._router() else [])
             if a.name == venue), None)
        broker: Optional[dict] = None
        if asset_class is None and adapter is not None and hasattr(adapter, "realized_stats"):
            try:
                broker = await adapter.realized_stats()
            except Exception as e:
                broker = {"source": "broker", "available": False,
                          "reason": f"{type(e).__name__}"}
            if broker.get("available"):
                try:
                    snapshot = await adapter.account()
                    broker["equity_usd"] = snapshot.equity_usd
                    broker["cash_usd"] = snapshot.cash_usd
                except Exception:
                    pass                      # the P&L is still worth showing
        return {
            "venue": venue,
            "asset_class": asset_class,
            "fund": fund,
            "broker": broker,
            "primary": "broker" if (broker or {}).get("available") else "fund",
        }

    # ---------- model spend ----------

    def costs(self) -> dict:
        """Burn versus earn, per trading mode.

        Earnings are attributed the only way the books allow: paper mode owns
        the realised P&L of simulated fills, and live mode's earnings stay
        `null` until a live venue has actually executed. Reporting an untested
        mode as $0.00 earned would read as break-even rather than as untried.
        """
        from cache.cost_ledger import CostLedger

        ledger = getattr(getattr(self.fund_scheduler, "fund", None), "cost_ledger", None)
        if ledger is None:
            router = getattr(getattr(self.fund_scheduler, "llm_router", None), "ledger", None)
            ledger = router or CostLedger(memory=self.memory)

        live_traded = any(
            (getattr(self._router(), "opened_at", {}) or {}).values()
        ) if self._router() else False
        # Robinhood is the only venue the fund trades, so it is the only one
        # whose earnings can pay for the tokens spent deciding them.
        paper_earned = self.record("robinhood")["realized_usd"]

        summary = ledger.summary(earnings={
            "paper": paper_earned,
            "live": 0.0 if live_traded else None,
        })
        summary["recent"] = self.memory.recent_llm_costs(limit=12)
        summary["note"] = (
            "Live earnings are unknown until a live venue executes; the fund has "
            "not been permitted to."
        ) if not live_traded else None
        return summary

    # ---------- venue engines ----------
    #
    # An "engine" is the connection to a venue: authenticated, reachable, and
    # allowed to open positions. It is the venue session under a name that says
    # what starting it actually does. Trading mode (paper vs live) is a separate
    # decision — see venue_modes.

    ENGINE_ADAPTERS = {"robinhood": ("robinhood", "paper"), "polymarket_us": ("polymarket_us",)}

    def engines(self) -> dict[str, dict]:
        """Per-venue connection state, and why it is not usable if it is not."""
        router = self._router()
        adapters = {a.name: a for a in (router.adapters if router else [])}
        running = getattr(self.fund_scheduler, "state", None) == "running"

        out: dict[str, dict] = {}
        for venue, candidates in self.ENGINE_ADAPTERS.items():
            adapter_name = next((c for c in candidates if c in adapters), None)
            adapter = adapters.get(adapter_name) if adapter_name else None
            authed, reason = self._venue_auth(venue, adapter)
            retired = is_retired(venue)
            out[venue] = {
                "adapter": adapter_name,
                "attached": adapter is not None,
                "authenticated": authed,
                # A retired venue's "reason" is its retirement, not a missing
                # adapter. Reporting "no adapter registered" would read as a
                # fixable configuration gap rather than a decision.
                "reason": retirement_reason(venue) if retired else reason,
                "retired": retired,
                "on": bool(not retired and adapter_name and router
                           and router.is_enabled(adapter_name)),
                # The engine is a no-op while the fund is stopped; saying so is
                # better than a button that appears to work and changes nothing.
                "system_running": running,
            }
        return out

    def _venue_auth(self, venue: str, adapter: Any) -> tuple[bool, Optional[str]]:
        if adapter is None:
            return False, "no adapter registered for this venue"
        session = getattr(adapter, "session", None)
        summary = getattr(session, "auth_summary", None)
        if summary is None:
            # A paper adapter has nothing to authenticate against, and saying
            # "authenticated" would overstate it.
            return True, None if getattr(adapter, "is_live", False) else "paper adapter — nothing to authenticate"
        try:
            info = summary() or {}
        except Exception as e:
            return False, f"auth check failed: {type(e).__name__}"
        if info.get("authenticated"):
            return True, None
        return False, "not authenticated — run scripts_mcp_auth.py once"

    def set_engine(self, venue: str, on: bool) -> dict:
        """Start or stop a venue's engine. Refuses while the fund is stopped:
        an engine that connects to nothing is a light, not a switch."""
        if venue not in self.ENGINE_ADAPTERS:
            return {"ok": False, "reason": f"unknown venue {venue!r}", "engines": self.engines()}
        if is_retired(venue):
            return {"ok": False, "reason": retirement_reason(venue),
                    "engines": self.engines()}
        state = self.engines()[venue]
        if not state["attached"]:
            return {"ok": False, "reason": state["reason"], "engines": self.engines()}
        if on and not state["system_running"]:
            return {"ok": False, "reason": "Project धन is stopped — start the system first",
                    "engines": self.engines()}
        if on and not state["authenticated"]:
            return {"ok": False, "reason": state["reason"], "engines": self.engines()}
        result = self.set_venue_session(state["adapter"], on)
        if not result.get("ok"):
            return {**result, "engines": self.engines()}
        return {"ok": True, "engines": self.engines()}

    # ---------- per-seat evaluation matrix ----------

    RECENT_WINDOW = 10

    def agent_matrix(self) -> list[dict]:
        """Per-seat examination: what they did, where they failed, what it costs
        them, and what clearing the bar would take.

        Three tenses, from three different sources:

        **Past** — hit rate, Brier and overconfidence over every scored call,
        from `roundtable.calibration`. Brier rather than accuracy, because being
        right 55% of the time while claiming 95% is the behaviour that costs
        money and accuracy cannot see it.

        **Present** — the same score over the last `RECENT_WINDOW` debates
        against everything before them. That delta is the improvement rate; a
        seat is not judged on a lifetime average it can no longer influence.

        **Future** — what the next calls have to look like. `required_hit_rate`
        is the bar, `gap` is the distance to it, and `enforced` says what the
        fund is ALREADY doing about the seat rather than what someone might do.

        Blame counts come from resolved losses where the seat's own signal
        matched the consensus. A dissenter is never counted, here or anywhere.
        """
        from roundtable.calibration import score_seats

        try:
            delibs = self.memory.recent_deliberations(limit=500)
            outcomes = {o["thesis_id"]: o for o in self.memory.resolved_outcomes(limit=500)}
        except Exception:
            return []

        overall = {s.seat_id: s for s in score_seats(delibs, outcomes).seats}
        # recent_deliberations is newest-first, so the head IS the recent window.
        recent = {s.seat_id: s for s in score_seats(delibs[: self.RECENT_WINDOW], outcomes).seats}
        prior = {s.seat_id: s for s in score_seats(delibs[self.RECENT_WINDOW :], outcomes).seats}

        blame = self._blame_by_seat()
        fit = (self.scorecard().get("fit") or {})
        shrink = fit.get("shrink")

        rows = []
        for agent in self.agents():
            sid = agent["id"]
            o, r, pr = overall.get(sid), recent.get(sid), prior.get(sid)
            samples = o.samples if o else 0
            hit = o.hit_rate * 100 if o and o.samples else None
            recent_hit = r.hit_rate * 100 if r and r.samples else None
            prior_hit = pr.hit_rate * 100 if pr and pr.samples else None
            improvement = (
                round(recent_hit - prior_hit, 1)
                if recent_hit is not None and prior_hit is not None else None
            )
            failure = blame.get(sid, {})
            rows.append({
                **agent,
                "samples": samples,
                "abstentions": o.abstentions if o else 0,
                "hit_rate": None if hit is None else round(hit, 1),
                "brier": round(o.brier, 4) if o and o.samples else None,
                "mean_confidence": round(o.mean_confidence, 1) if o and o.samples else None,
                "overconfidence": round(o.overconfidence, 1) if o and o.samples else None,
                "calibrated": bool(o and o.is_calibrated),
                "beats_coin_flip": bool(o and o.beats_a_coin_flip),
                "recent": {"window": self.RECENT_WINDOW, "samples": r.samples if r else 0,
                           "hit_rate": None if recent_hit is None else round(recent_hit, 1)},
                "prior": {"samples": pr.samples if pr else 0,
                          "hit_rate": None if prior_hit is None else round(prior_hit, 1)},
                "improvement_pts": improvement,
                "blamed_losses": failure.get("count", 0),
                "top_failure": failure.get("top"),
                "failure_note": failure.get("note"),
                "enforced": self._enforcement_for(o, shrink),
                "target": self._target_for(o),
            })
        return rows

    def _blame_by_seat(self) -> dict[str, dict]:
        """Losses each seat backed, and the postmortem code that names why.

        Gated. The fund is long-only and `correct` is `realized > 0`, so every
        seat that voted bullish is scored on the IDENTICAL event — per-seat
        blame rates below the bar are noise by construction. The per-TRADE
        attribution in `trade_history` is unaffected: that is a transcript of
        one displayed trade, not a rate computed across trades.
        """
        from collections import Counter
        from roundtable.calibration import MIN_SAMPLES_FOR_SEAT_SCORE
        try:
            if len(self.memory.resolved_outcomes(limit=MIN_SAMPLES_FOR_SEAT_SCORE)) \
                    < MIN_SAMPLES_FOR_SEAT_SCORE:
                return {}
        except Exception:
            return {}
        counts: dict[str, Counter] = {}
        totals: dict[str, int] = {}
        notes: dict[str, str] = {}
        for row in self.trade_history(limit=100):
            if row["won"]:
                continue
            codes = [a["code"] for a in row.get("actions", []) if a.get("code")]
            for blamed in row.get("blamed", []):
                sid = self._seat_id_for(blamed.get("name"))
                if sid is None:
                    continue
                totals[sid] = totals.get(sid, 0) + 1
                counts.setdefault(sid, Counter()).update(codes)
                if sid not in notes and row.get("actions"):
                    notes[sid] = row["actions"][0]["lesson"]
        out = {}
        for sid, total in totals.items():
            common = counts[sid].most_common(1)
            out[sid] = {
                "count": total,
                "top": {"code": common[0][0], "count": common[0][1]} if common else None,
                "note": notes.get(sid),
            }
        return out

    def _seat_id_for(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        for a in self.agents():
            if a["name"] == name:
                return a["id"]
        return None

    def _enforcement_for(self, score, shrink) -> dict:
        """What the fund ALREADY does to this seat's number.

        Split deliberately: `applied` is machinery that runs today, `flagged` is
        a recommendation. Printing a recommendation as though it were enforced
        would make the dashboard describe a fund that does not exist.

        The value is read back off the PIPELINE — the object that actually
        multiplies by it — not re-derived here. A displayed enforcement that is
        computed separately from the enforcing code drifts the moment one of
        them changes, which is exactly how this panel came to claim a fitted
        shrink was governing sizing while the constant was.
        """
        from trading.pipeline import CONFIDENCE_SHRINK
        pipeline = getattr(getattr(self.fund_scheduler, "fund", None), "pipeline", None)
        in_force = getattr(pipeline, "confidence_shrink", None)
        fitted = shrink is not None and in_force is not None and abs(in_force - shrink) < 1e-9
        used = in_force if in_force is not None else CONFIDENCE_SHRINK
        applied = [
            f"stated confidence scaled by {used:.2f} before sizing"
            + (" (fitted from resolved outcomes)" if fitted
               else " (pessimistic default — not yet fittable)")
        ]
        flagged = []
        if score and score.samples:
            if score.overconfidence > 10:
                flagged.append(
                    f"overstates by {score.overconfidence:.0f} points — its own shrink should be tighter")
            elif score.overconfidence < -10:
                flagged.append(
                    f"understates by {abs(score.overconfidence):.0f} points — its calls are worth more than it claims")
            if not score.beats_a_coin_flip:
                flagged.append(f"Brier {score.brier:.2f} is no better than always saying 50%")
        if score and score.abstentions and score.samples == 0:
            flagged.append("has never completed a call — every appearance was an abstention")
        return {"applied": applied, "flagged": flagged}

    def _target_for(self, score) -> dict:
        """What the next calls have to look like to clear the bar."""
        from roundtable.calibration import MIN_SAMPLES_FOR_FIT
        if score is None or not score.samples:
            return {"required_hit_rate": 50.0, "gap": None,
                    "note": f"unscored — {MIN_SAMPLES_FOR_FIT} resolved theses before a fit means anything"}
        required = 50.0
        gap = round(required - score.hit_rate * 100, 1)
        if gap > 0:
            note = f"needs +{gap:.0f} points of hit rate to beat chance"
        elif abs(score.overconfidence) > 10:
            note = "hit rate is fine; the confidence attached to it is not"
        else:
            note = "clearing the bar — hold it over more samples"
        return {"required_hit_rate": required, "gap": gap, "note": note}

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
