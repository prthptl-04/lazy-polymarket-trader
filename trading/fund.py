"""The fund loop — one cycle of the autonomous hedge fund.

A cycle is:

    observe equity  →  read the session  →  pick the tradable universe
                    →  build candidates (deterministic screens)
                    →  pre-screen veto
                    →  round table (only for survivors)
                    →  pipeline: size, grade, route, submit

and, on the weekend-to-weekday handoff, flatten crypto before the equity open.

Two orderings here are load-bearing rather than stylistic:

**Equity is observed before anything is decided.** The kill-switch can only
block what it has seen, so the mark goes in first. A cycle that traded before
observing would be trading with the switch unarmed.

**The pre-screen runs before the round table.** Screens cost microseconds; a
deliberation costs six LLM calls. Anything disqualifying on arithmetic — a
distress-zone balance sheet, no buildable stop — must never reach a seat.

What the session decides
------------------------
Mon–Fri the equities universe is live (premarket through after-hours); nights,
weekends and market holidays are crypto-only. That rotation is not a preference
— it is what the venue calendar permits, and `trading/sessions.py` is the only
place it is expressed.

Nothing in this file can bypass a gate. It orchestrates; the grader and the
router decide.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from roundtable.postmortem import Postmortem, recent_lesson_lines
from trading.candidate_builder import build_candidate
from trading.fund_config import is_thesis_stale
from trading.pipeline import EXTENDED_HOURS_CUSHION_BPS
from trading.sessions import Session, session_at, should_flatten_crypto
from trading.venues.base import OrderRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Holding:
    symbol: str
    asset_class: str
    quantity: float


@dataclass
class CycleReport:
    moment: datetime
    session: str
    universe: list[str] = field(default_factory=list)
    prescreened_out: list[dict] = field(default_factory=list)
    deliberated: list[str] = field(default_factory=list)
    results: list[Any] = field(default_factory=list)     # PipelineResult
    flattened: list[str] = field(default_factory=list)
    exits: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    halted_reason: Optional[str] = None

    @property
    def submitted(self) -> list[Any]:
        return [r for r in self.results if getattr(r, "submitted", False)]

    def summary(self) -> dict:
        return {
            "moment": self.moment.isoformat(),
            "session": self.session,
            "universe": len(self.universe),
            "prescreened_out": len(self.prescreened_out),
            "deliberated": len(self.deliberated),
            "submitted": len(self.submitted),
            "flattened": len(self.flattened),
            "exits": len(self.exits),
            "errors": len(self.errors),
            "halted_reason": self.halted_reason,
        }


@dataclass
class FundLoop:
    """Runs cycles. Construct once; call `run_cycle` on a schedule."""

    router: Any
    pipeline: Any
    round_table: Any
    data: Any                                   # MarketDataProvider
    equity_watchlist: Sequence[str] = ()
    crypto_watchlist: Sequence[str] = ()
    kill_switch: Any = None
    position_book: Any = None
    cost_ledger: Any = None                  # cache.cost_ledger.CostLedger
    corroborator: Any = None                 # roundtable.corroborator.Corroborator
    scout: Any = None
    memory: Any = None
    postmortem: Any = None
    lookback_bars: int = 60
    max_candidates_per_cycle: int = 5
    resume_max_age_seconds: float = 3600.0
    on_cycle: Any = None

    # ---------- one cycle ----------

    async def run_cycle(
        self,
        moment: datetime,
        *,
        holdings: Sequence[Holding] = (),
        equity_usd: Optional[float] = None,
        available_cash_usd: Optional[float] = None,
    ) -> CycleReport:
        session = session_at(moment)
        report = CycleReport(moment=moment, session=session.value)

        # 1. Show the mark to the kill-switch BEFORE anything is decided.
        if self.kill_switch is not None and equity_usd is not None:
            self.kill_switch.observe_equity(moment, equity_usd)

        # 2. Exits first, ALWAYS. A stop that has fired must be honoured before
        #    anything else happens — before the kill-switch check (which permits
        #    closes anyway) and before a single token is spent on a new idea.
        if self.position_book is not None:
            report.exits = await self._process_exits(moment, report)

        # 3. Weekend → weekday handoff: free the capital before the open.
        if should_flatten_crypto(moment):
            report.flattened = await self._flatten_crypto(holdings, moment, report)

        # 4. A tripped switch stops new risk. Exits above already happened,
        #    and the router would permit more, but there is no reason to spend
        #    six LLM calls on a thesis that cannot be acted on.
        if self.kill_switch is not None:
            verdict = self.kill_switch.evaluate(moment)
            if not verdict.allowed:
                report.halted_reason = verdict.reason
                self._emit(report)
                return report

        # 5. Universe follows the session.
        universe = self._universe_for(session)
        report.universe = list(universe)
        if not universe:
            self._emit(report)
            return report

        # Size against what the account actually holds. A config bankroll that
        # drifts from the real balance either over-sizes into a rejection or
        # leaves capital idle; the wallet is the truth.
        if equity_usd and hasattr(self.pipeline, "bankroll_usd"):
            self.pipeline.bankroll_usd = equity_usd

        held = {h.symbol: h for h in holdings}

        # 6. Build + screen + deliberate + execute.
        deliberated = 0
        for symbol in universe:
            if deliberated >= self.max_candidates_per_cycle:
                break
            try:
                outcome = await self._consider(
                    symbol, session, moment, held,
                    open_positions=len(holdings),
                    available_cash_usd=available_cash_usd,
                    report=report,
                )
                if outcome:
                    deliberated += 1
            except Exception as e:
                # One bad symbol must not end the cycle.
                logger.exception("cycle failed on %s", symbol)
                report.errors.append(f"{symbol}: {type(e).__name__}: {e}")

        self._emit(report)
        return report

    # ---------- per symbol ----------

    async def _consider(
        self,
        symbol: str,
        session: Session,
        moment: datetime,
        held: dict[str, Holding],
        *,
        open_positions: int,
        available_cash_usd: Optional[float],
        report: CycleReport,
    ) -> bool:
        """Returns True if the round table was actually convened."""
        asset_class = "crypto" if symbol in self.crypto_watchlist else "equity"

        history = await self.data.get_history(symbol, lookback=self.lookback_bars)
        quote = await self.data.get_quote(symbol)
        price = (quote.mid if quote else None) or (
            history.closes[-1] if history and history.closes else None
        )
        if history is None or price is None:
            report.prescreened_out.append(
                {"symbol": symbol, "reason": "no price data available"}
            )
            return False

        sentiment_notes = await self._news_notes(symbol)
        corroboration_notes = await self._corroboration_notes(symbol)
        financials = await self.data.get_financials(symbol)
        current_fin, prior_fin = financials if financials else (None, None)

        built = build_candidate(
            symbol=symbol,
            bars=history.bars,
            price=price,
            asset_class=asset_class,
            session=session.value,
            spread_bps=quote.spread_bps if quote else None,
            returns=history.returns,
            dollar_volumes=history.dollar_volumes,
            financials=current_fin,
            prior_financials=prior_fin,
            sentiment_notes=sentiment_notes,
            corroboration_notes=corroboration_notes,
            portfolio_notes=self._portfolio_notes(symbol, held, moment),
            lessons=recent_lesson_lines(self.memory),
            budget_notes=self._budget_notes(),
        )

        if not built.prescreen.worth_debating:
            report.prescreened_out.append(
                {"symbol": symbol, "reason": built.prescreen.reason,
                 "rejected_by": built.prescreen.rejected_by}
            )
            return False

        thesis = await self.round_table.deliberate(built.candidate)
        report.deliberated.append(symbol)

        holding = held.get(symbol)
        result = await self.pipeline.run(
            thesis, built.candidate, built.exit_plan, moment,
            held_quantity=holding.quantity if holding else 0.0,
            open_positions=open_positions,
            available_cash_usd=available_cash_usd,
        )
        report.results.append(result)

        # Register the fill so the stop is actually watched from here on. A
        # position opened without this entry would ride through its stop.
        if (
            self.position_book is not None
            # `filled`, not `submitted`: an accepted resting limit order has not
            # traded, and extended-hours candidates are ALWAYS limit orders.
            and result.filled
            and result.trade is not None
            and result.trade.is_entry
            and result.size is not None
        ):
            entry_fill = self._fill_price(result.ack)
            filled_qty = self._filled_quantity(result.ack)
            self.position_book.open(
                symbol=symbol,
                asset_class=asset_class,
                # What the venue holds, not what the sizer asked for.
                quantity=filled_qty if filled_qty is not None else result.size.quantity,
                # The venue's price, not the mid the plan was drawn on.
                entry_price=entry_fill if entry_fill is not None else built.exit_plan.entry,
                planned_entry=built.exit_plan.entry,
                entry_fill_source="venue" if entry_fill is not None else "mid",
                spread_bps_at_entry=built.candidate.spread_bps,
                mode=self._venue_mode(result.ack),
                plan=built.exit_plan,
                thesis_id=thesis.thesis_id,
                # Carried so the outcome can be scored against what was staked.
                signal=thesis.consensus.signal if thesis.consensus else None,
                confidence=thesis.consensus.confidence if thesis.consensus else None,
                venue=result.ack.venue if result.ack else None,
            )
        return True

    async def _corroboration_notes(self, symbol: str) -> tuple[str, ...]:
        """Cross-check the primary provider's figures against a second source.

        The Corroborator seat has been voting on an EMPTY corroboration block:
        the module was written and tested and never wired, so `build_candidate`
        had no parameter to pass it through. A seat reasoning from nothing is
        worse than an absent seat — it adds apparent independence to the tally.

        Off the hot path by construction (one provider round-trip per candidate
        on a multi-minute cycle), and `scrape=False` keeps a browser out of it
        per rules #16 and #20.
        """
        if self.corroborator is None:
            return ()
        try:
            report = await self.corroborator.run(symbol, scrape=False)
        except Exception:
            logger.exception("corroboration failed for %s", symbol)
            # Silence, not a reassuring note: the seat must not read a failed
            # cross-check as a clean one.
            return ("Corroboration unavailable — this cycle could not build a "
                    "second fact set. Treat every figure as single-sourced.",)
        return tuple(report.evidence_lines())

    async def _news_notes(self, symbol: str) -> tuple[str, ...]:
        """Headlines for the Sentiment seat.

        Labelled with the PUBLISHER's sentiment, explicitly, because a vendor's
        label is a data point and not a verdict — the seat is instructed to
        treat crowded agreement as a risk factor rather than a confirmation.
        """
        getter = getattr(self.data, "get_news", None)
        if getter is None:
            return ()
        try:
            articles = await getter(symbol, limit=5)
        except Exception:
            logger.exception("news fetch failed for %s", symbol)
            return ()
        notes = []
        for a in articles:
            label = a.get("sentiment") or "unlabelled"
            line = f"[publisher sentiment: {label}] {a.get('title') or ''}"
            if a.get("why"):
                line += f" — {a['why'][:180]}"
            notes.append(line)
        return tuple(notes)

    # ---------- exits ----------

    async def _process_exits(self, moment: datetime, report: CycleReport) -> list[dict]:
        """Close anything that hit its stop or target.

        Sized in QUANTITY, never notional — a dollar-sized close overshoots
        once the price has moved, which is exactly the state a stopped-out
        position is in.
        """
        quotes = await self._quotes(self.position_book.open_symbols())
        if not quotes:
            return []
        marks = {s: q.mid for s, q in quotes.items()}

        session = session_at(moment)
        done: list[dict] = []
        for signal in self.position_book.check_exits(marks):
            try:
                # An exit used to go out as a plain market order. The router
                # refuses equity market orders in extended hours, so a stop that
                # fired premarket or after-hours could not execute AT ALL — the
                # fund could enter in a session it was unable to leave.
                exit_kwargs = self._exit_order_kwargs(
                    quotes.get(signal.symbol), session, signal.position.asset_class)
                if exit_kwargs is None:
                    report.errors.append(
                        f"EXIT UNPRICEABLE {signal.symbol} ({signal.reason}): "
                        f"{session.value} needs a two-sided quote to price a "
                        "marketable limit; the position stays open and will retry"
                    )
                    continue
                ack = await self.router.place(
                    OrderRequest(
                        symbol=signal.symbol, side="sell",
                        asset_class=signal.position.asset_class,
                        quantity=signal.quantity,
                        thesis_id=signal.position.thesis_id,
                        **exit_kwargs,
                    ),
                    moment,
                )
                if ack.is_filled:
                    record = self._book_close(
                        signal.symbol, ack,
                        planned_price=signal.price, reason=signal.reason,
                    )
                    entry = {**(record or {}), "detail": signal.describe()}
                    # A loss is the only thing the fund learns from for certain.
                    entry["lessons"] = self._postmortem(record, signal)
                    done.append(entry)
                else:
                    # The position stays open and will be retried next tick.
                    # Silently dropping a failed stop would be the worst
                    # possible outcome here, so it goes in the report. An
                    # ACCEPTED but unfilled exit lands here too — it is resting
                    # at the venue, and the position is still ours until it
                    # trades.
                    report.errors.append(
                        f"EXIT FAILED {signal.symbol} ({signal.reason}): "
                        f"{ack.error or f'accepted but unfilled (status={ack.status})'}"
                    )
            except Exception as e:
                report.errors.append(
                    f"EXIT FAILED {signal.symbol}: {type(e).__name__}: {e}"
                )
        return done

    async def _quotes(self, symbols: Sequence[str]) -> dict[str, Any]:
        """Full quotes, not just mids — an extended-hours exit has to be priced
        through the touch, and a mid cannot say where the touch is."""
        out: dict[str, Any] = {}
        for symbol in symbols:
            try:
                quote = await self.data.get_quote(symbol)
            except Exception:
                continue
            if quote is not None and quote.mid is not None:
                out[symbol] = quote
        return out

    async def _quote_map(self, symbols: Sequence[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for symbol in symbols:
            try:
                quote = await self.data.get_quote(symbol)
            except Exception:
                continue
            price = quote.mid if quote else None
            if price is not None:
                out[symbol] = price
        return out

    @staticmethod
    def _exit_order_kwargs(quote: Any, session: Any, asset_class: str) -> Optional[dict]:
        """How to word an exit so the router will take it in this session.

        Regular hours and crypto: a market order. Extended hours on equities:
        a limit priced THROUGH the bid, because the router refuses both market
        orders and limits that are not marked extended_hours. Returns None when
        there is no two-sided quote to price against — a stop that cannot be
        priced is a loud error, never a resting order nobody is watching.
        """
        if asset_class != "equity" or not getattr(session, "is_extended_hours", False):
            return {"order_type": "market"}
        bid = getattr(quote, "bid", None)
        spread = getattr(quote, "spread_bps", None) if quote else None
        if bid is None or spread is None:
            return None
        through = (spread / 2 + EXTENDED_HOURS_CUSHION_BPS) / 10_000
        return {
            "order_type": "limit",
            "limit_price": round(bid * (1 - through), 4),
            "extended_hours": True,
        }

    @staticmethod
    def _filled_quantity(ack: Any) -> Optional[float]:
        """How much the venue actually bought.

        A notional order resolves to `size_usd / fill_price`, and the fill is
        never the mid the plan was sized against — so the book recording
        `size.quantity` holds MORE than the venue does. The exit then sells a
        quantity that does not exist and comes back "cannot sell 5.0: holding
        4.9925", which means a stop that fires cannot execute. Latent until the
        fill price was captured; fatal the first time a stop mattered.
        """
        raw = getattr(ack, "raw", None) or {}
        quantity = raw.get("quantity")
        try:
            return float(quantity) if quantity is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fill_price(ack: Any) -> Optional[float]:
        """What the venue actually traded at, or None if it did not say.

        PaperVenue crosses the spread and adds slippage and stamps the result
        into ack.raw["fill_price"]. Booking the mid instead — which is what the
        fund did — computes every P&L figure, every realised return and the
        fitted shrink on a cost-free round trip, while the grader is rejecting
        trades on a slippage estimate. Graded as if cost matters, scored as if
        it does not.
        """
        raw = getattr(ack, "raw", None) or {}
        price = raw.get("fill_price")
        try:
            return float(price) if price is not None else None
        except (TypeError, ValueError):
            return None

    def _book_close(self, symbol: str, ack: Any, *, planned_price: float,
                    reason: str) -> Optional[dict]:
        """Remove a position from the book at the price it actually traded.

        The single funnel for every close — stops and targets, a bearish
        consensus on a held name, and the weekend crypto flatten. Two of those
        three used to sell at the venue and never touch the book, leaving a
        position that fired exits forever against inventory the fund no longer
        owned and produced neither a closed_trades row nor a thesis outcome.
        """
        if self.position_book is None:
            return None
        fill = self._fill_price(ack)
        return self.position_book.close(
            symbol, fill if fill is not None else planned_price, reason=reason,
            planned_exit=planned_price,
            exit_fill_source="venue" if fill is not None else "mid",
        )

    def _postmortem(self, record: Optional[dict], signal: Any) -> list[str]:
        """Turn a losing exit into lessons the seats read next time."""
        if self.postmortem is None or not record:
            return []
        if record.get("realized_return", 0) >= 0:
            return []
        thesis = None
        thesis_id = record.get("thesis_id")
        if thesis_id and self.memory is not None:
            try:
                thesis = self.memory.get_deliberation(thesis_id)
            except Exception:
                thesis = None
        try:
            findings = self.postmortem.run(
                symbol=record["symbol"],
                realized_return=record["realized_return"],
                thesis=thesis,
                exit_reason=record.get("reason", "stop"),
                # The plan the position was opened under. Stop quality can only
                # be judged against the stop that was actually set.
                plan={"entry": record.get("entry_price"),
                      "stop": record.get("stop"),
                      "atr": record.get("atr")},
            )
        except Exception:
            logger.exception("post-mortem failed for %s", record.get("symbol"))
            return []
        return [f.code for f in findings]

    # ---------- weekend handoff ----------

    async def _flatten_crypto(
        self, holdings: Sequence[Holding], moment: datetime, report: CycleReport
    ) -> list[str]:
        """Close crypto before the equity open so the capital is available.

        Sized in quantity, not notional — a dollar-sized close overshoots once
        the price has moved, and these positions have been open all weekend.
        """
        closed: list[str] = []
        for holding in holdings:
            if holding.asset_class != "crypto" or holding.quantity <= 0:
                continue
            try:
                ack = await self.router.place(
                    OrderRequest(
                        symbol=holding.symbol, side="sell",
                        asset_class="crypto", quantity=holding.quantity,
                    ),
                    moment,
                )
                if ack.is_filled:
                    # Was a pure venue sale: the book kept the position and went
                    # on checking a stop against inventory that was gone.
                    self._book_close(
                        holding.symbol, ack,
                        planned_price=self._fill_price(ack) or 0.0,
                        reason="flatten",
                    )
                    closed.append(holding.symbol)
                else:
                    report.errors.append(
                        f"flatten {holding.symbol}: "
                        f"{ack.error or f'accepted but unfilled (status={ack.status})'}"
                    )
            except Exception as e:
                report.errors.append(f"flatten {holding.symbol}: {type(e).__name__}: {e}")
        return closed

    # ---------- helpers ----------

    def _universe_for(self, session: Session) -> list[str]:
        """Configured watchlist if one exists, otherwise the scout screens the
        whole tape. A configured list is an override, not the normal path."""
        if not session.equities_open:
            return list(self.crypto_watchlist)
        if self.equity_watchlist:
            return list(self.equity_watchlist)
        if self.scout is None:
            return []
        try:
            return [c.symbol for c in self.scout.scan(limit=self.max_candidates_per_cycle * 2)]
        except Exception as e:
            logger.exception("scout scan failed")
            return []

    def _budget_notes(self) -> tuple[str, ...]:
        """The burn-versus-earn line the seats read before every debate.

        Deliberately not a hard budget cap: refusing to think because a counter
        crossed a threshold is how a fund stops reacting to a market. It is
        shown so the seats can be brief when brevity is free and thorough when
        it is not.
        """
        ledger = getattr(self, "cost_ledger", None)
        if ledger is None:
            return ()
        try:
            earnings = {"paper": self._realized_usd(), "live": None}
            brief = ledger.brief(ledger.summary(earnings=earnings))
        except Exception:
            return ()
        return tuple(brief.splitlines()) if brief else ()

    def _realized_usd(self) -> Optional[float]:
        book = getattr(self, "position_book", None)
        closed = list(getattr(book, "closed", []) or []) if book else []
        return round(sum(c.get("realized_usd", 0.0) for c in closed), 4) if closed else None

    def _portfolio_notes(
        self, symbol: str, held: dict[str, Holding], moment: datetime
    ) -> tuple[str, ...]:
        notes = []
        holding = held.get(symbol)
        if holding:
            notes.append(f"Already holding {holding.quantity} of {symbol}")
        else:
            notes.append(f"No existing position in {symbol}")
        notes.append(f"{len(held)} open positions across the book")

        pdt = getattr(self.router, "pdt", None)
        if pdt is not None:
            status = pdt.status(moment)
            if status["pdt_applies"]:
                notes.append(
                    f"Day-trade budget: {status['day_trades_remaining']} of 3 remaining "
                    "in the rolling 5-business-day window"
                )
        if self.kill_switch is not None:
            ks = self.kill_switch.status(moment)
            if ks["armed"]:
                notes.append(
                    f"Daily loss headroom: ${ks['remaining_usd']:,.2f} of "
                    f"${ks['limit_usd']:,.2f}"
                )
        return tuple(notes)

    def _emit(self, report: CycleReport) -> None:
        if self.memory is not None:
            try:
                self.memory.record_audit_event(
                    "fund_loop", "cycle_complete", report.session, report.summary()
                )
            except Exception:
                logger.exception("failed to audit cycle")
        if self.on_cycle is not None:
            try:
                self.on_cycle(report)
            except Exception:
                logger.exception("on_cycle callback failed")

    # ---------- resume ----------

    async def resume_unfinished(self, *, now: Optional[float] = None) -> list[str]:
        """Return interrupted theses that are still fresh enough to act on.

        A deliberation reasons about prices at a moment in time, so resuming
        one built on yesterday's tape would apply a stale conclusion to a
        market that has moved. Anything older than `resume_max_age_seconds` is
        marked `abandoned` in memory rather than surfaced — otherwise every GO
        would report the same weeks-old ghosts forever.

        Fresh theses are reported, not auto-run. Deciding to re-run is the
        caller's call.
        """
        if self.memory is None:
            return []
        current = now if now is not None else time.time()
        fresh: list[str] = []
        try:
            rows = self.memory.unfinished_deliberations()
        except Exception:
            logger.exception("failed to read unfinished deliberations")
            return []

        for row in rows:
            if is_thesis_stale(
                row.get("created"),
                max_age_seconds=self.resume_max_age_seconds,
                now=current,
            ):
                self._abandon(row)
            else:
                fresh.append(row["thesis_id"])
        return fresh

    def _abandon(self, row: dict) -> None:
        try:
            self.memory.save_deliberation(
                thesis_id=row["thesis_id"],
                symbol=row["symbol"],
                asset_class=row["asset_class"],
                status="abandoned",
                payload=row.get("payload") or {},
                signal=row.get("signal"),
                confidence=row.get("confidence"),
            )
        except Exception:
            logger.exception("failed to abandon stale thesis %s", row.get("thesis_id"))
