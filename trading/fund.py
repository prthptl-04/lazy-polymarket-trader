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
from trading.sessions import EASTERN, Session, session_at, should_flatten_crypto
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
    # The calibration in force for this cycle. A learning loop nobody can see
    # is a learning loop nobody will notice breaking.
    confidence_shrink: Optional[float] = None
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
            "confidence_shrink": self.confidence_shrink,
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
    # symbol -> the trading day it was last closed on. See `_not_cooling_off`.
    _cooling_off: dict = field(default_factory=dict)
    # Optional. `monitoring.telegram.TelegramNotifier`, or anything with
    # `.notify(str)`. Never on the hot path and never load-bearing: a dead
    # notifier loses a message, not a trade.
    notifier: Any = None
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

        # 0. Re-read what the committee has actually achieved, and let it
        #    resize the next decisions. `fit_confidence_shrink` maps stated
        #    confidence onto realised hit rate, and it used to be fitted ONCE
        #    in build_fund — so a daemon running for days learned nothing until
        #    it was restarted, and every outcome recorded while running was
        #    ignored.
        #
        #    At the CYCLE boundary, not per trade: two trades in the same cycle
        #    must not size differently for reasons unrelated to either thesis.
        #    That was the reason the original was pinned at build, and it is
        #    preserved — the value is fixed for the whole cycle below.
        # 0a. Reconcile anything that filled while we were not looking. A
        #     resting limit fills when the market comes to it, which is between
        #     cycles by definition — and a fill the book does not know about is
        #     inventory the fund will act as though it does not hold.
        self._reconcile_resting(report)

        self._recalibrate()
        report.confidence_shrink = getattr(self.pipeline, "confidence_shrink", None)

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
        universe = self._universe_for(session, moment)
        report.universe = list(universe)
        if not universe:
            # A cycle that reports nothing at all is indistinguishable from a
            # quiet market. Say which it is — an unconfigured weekend is most
            # of the week under rule #23, and it used to be silent.
            if not session.equities_open and not self.crypto_watchlist:
                report.errors.append(
                    "no crypto watchlist configured, so there is nothing to "
                    "trade while equities are shut. Set FUND_CRYPTO_WATCHLIST "
                    "(e.g. BTC,ETH) — the scout screens the US equity tape and "
                    "has no crypto equivalent."
                )
            elif should_flatten_crypto(moment):
                report.errors.append(
                    "inside the weekend handoff window: the crypto book is "
                    "being flattened for the equity open, so no new positions "
                    "are opened this cycle"
                )
            self._emit(report)
            return report

        # Size against what the account actually holds. A config bankroll that
        # drifts from the real balance either over-sizes into a rejection or
        # leaves capital idle; the wallet is the truth.
        if equity_usd and hasattr(self.pipeline, "bankroll_usd"):
            self.pipeline.bankroll_usd = equity_usd

        # Keyed on BOTH crypto spellings. The venue names a position
        # "BTC-USD" (the pair actually traded) while a watchlist may say "BTC",
        # and a miss here is invisible: the fund concludes it holds nothing,
        # re-buys what it already owns, and the weekend flatten finds nothing
        # to close.
        held: dict[str, Holding] = {}
        for h in holdings:
            held[h.symbol] = h
            base = h.symbol.split("-")[0].upper()
            if h.asset_class == "crypto":
                held.setdefault(base, h)
                held.setdefault(f"{base}-USD", h)

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
        # `filled`, not `submitted`: an accepted resting limit order has not
        # traded, and extended-hours candidates are ALWAYS limit orders. The
        # same three conditions gate both directions, so they are hoisted.
        booked = (self.position_book is not None
                  and result.filled
                  and result.trade is not None)

        # `result.size is not None` belongs to the ENTRY branch only — a close
        # is never sized (`ThesisPipeline._close` skips the sizer), so requiring
        # it here would silently skip every close. That is the exact shape of
        # the bug this block was rewritten to fix.
        if booked and result.trade.is_entry and result.size is not None:
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
            self._announce({
                "symbol": symbol, "side": "buy",
                "quantity": filled_qty if filled_qty is not None else result.size.quantity,
                "price": entry_fill if entry_fill is not None else built.exit_plan.entry,
                "mode": self._venue_mode(result.ack),
                "venue": result.ack.venue if result.ack else None,
            })
        elif booked and not result.trade.is_entry:
            # A close that sold at the venue and never reached the book left a
            # phantom: it fired an exit every cycle against inventory the fund
            # no longer owned ("cannot sell 19.99 of AAPL: holding 0.0"), wrote
            # no closed_trades row, resolved no thesis, and inflated
            # unrealized_usd for ever. `_book_close`'s docstring already called
            # itself the single funnel for all three close types; this is the
            # caller that made that true.
            self._start_cooldown(symbol, moment)
            record = self._book_close(
                symbol, result.ack,
                # The mid the close was GRADED on — the same number written to
                # the trade ledger, so `planned_exit` reconciles against it.
                # `_book_close` prefers the venue's fill and falls back to this,
                # never to 0.0.
                planned_price=result.trade.entry,
                reason="signal",
            )
            if record is None:
                report.errors.append(
                    f"UNBOOKED CLOSE {symbol}: the venue sold but the book held "
                    "no position, so neither a closed trade nor a thesis outcome "
                    "was written. The book is in-memory and does not survive a "
                    "restart, which is the usual cause."
                )
            else:
                sold = self._filled_quantity(result.ack)
                if sold is not None and abs(sold - record["quantity"]) > 1e-6:
                    report.errors.append(
                        f"CLOSE QUANTITY MISMATCH {symbol}: the venue sold {sold} "
                        f"but the book closed {record['quantity']}; the realised "
                        "figure is computed on the book's quantity"
                    )
        return True

    def _reconcile_resting(self, report: CycleReport) -> None:
        """Book any resting order the market reached since the last cycle.

        Reported rather than silent: these are positions nobody decided on THIS
        cycle, and a fill that appears in the book with no deliberation behind
        it should be visible. The venue owns the matching; the fund only
        records what came back.
        """
        matcher = getattr(self.router, "adapters", None)
        for adapter in matcher or []:
            match = getattr(adapter, "match_resting", None)
            if match is None:
                continue
            try:
                for ack in match():
                    report.errors.append(
                        f"RESTING FILL {ack.raw.get('symbol', '?')}: an order "
                        f"placed on an earlier cycle filled at "
                        f"{ack.raw.get('fill_price')}. It is held at the venue."
                    )
            except Exception:
                logger.exception("could not reconcile resting orders")

    def _not_cooling_off(self, names: list[str], moment: datetime) -> list[str]:
        """Drop names this session already closed.

        A stop fires for a reason. Re-entering the symbol in the same session —
        at the stop price, seconds later — overrides a risk decision the fund
        made itself, and in paper it manufactures round trips that count toward
        the fifty rule #13 requires. Crypto needs this most: it is PDT-exempt,
        so the weekend book has no other brake at all.

        A cooldown, not a ban: the entry is keyed on the trading day and the
        name is tradable again next session.
        """
        today = _trading_day(moment)
        self._cooling_off = {s: d for s, d in self._cooling_off.items() if d >= today}
        return [n for n in names if self._cooling_off.get(n) != today]

    def _announce(self, fill: dict) -> None:
        """Tell the operator what the venue did. Never raises.

        Called only on a FILL. An accepted resting order has not traded, and
        announcing one would report a position that does not exist.
        """
        if self.notifier is None:
            return
        try:
            from monitoring.telegram import format_fill
            self.notifier.notify(format_fill(fill))
        except Exception:
            logger.warning("could not send a fill notification")

    def _start_cooldown(self, symbol: str, moment: datetime) -> None:
        self._cooling_off[symbol] = _trading_day(moment)

    def _recalibrate(self) -> None:
        """Refit the confidence shrink from resolved outcomes.

        Refuses by default. Too few rows, a bad fit, or any exception leaves
        whatever is currently in force standing — sizing must never be loosened
        by a thin sample or a broken read, and `fit_confidence_shrink` already
        declines below `MIN_SAMPLES_FOR_FIT` and clamps what it does return.
        """
        if self.memory is None or self.pipeline is None:
            return
        try:
            from roundtable.calibration import fit_confidence_shrink
            fit = fit_confidence_shrink(self.memory.resolved_outcomes(limit=1000))
        except Exception:
            logger.exception("could not refit the confidence shrink")
            return
        if fit.usable and fit.shrink is not None:
            self.pipeline.confidence_shrink = fit.shrink

        # And re-grade the seats themselves. The scorecard has always measured
        # them; until now nothing consumed it, so a seat with a Brier of 0.30
        # that had been wrong for fifty trades voted exactly as loudly as one
        # at 0.15. Refreshed at the same cycle boundary as the shrink, for the
        # same reason: grading computed once at boot is grading the fund cannot
        # act on.
        table = getattr(self, "round_table", None)
        if table is not None and hasattr(table, "seat_weights"):
            try:
                from roundtable.calibration import score_seats, seat_weights
                delibs = self.memory.recent_deliberations(limit=500)
                outcomes = {o["thesis_id"]: o
                            for o in self.memory.resolved_outcomes(limit=500)}
                table.seat_weights = seat_weights(
                    score_seats(delibs, outcomes).seats)
            except Exception:
                logger.exception("could not re-grade the seats")

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
        open_symbols = self.position_book.open_symbols()
        quotes = await self._quotes(open_symbols)

        # A position we cannot mark is a position whose stop is NOT being
        # watched. That used to be silent — an early return, and a cycle report
        # reading `exits: 0`, which is indistinguishable from "nothing hit its
        # stop". An hour of feed outage left every stop in the book unwatched
        # and said nothing. Naming the symbols is the difference between a
        # degraded cycle and an invisible one.
        for symbol in open_symbols:
            if symbol not in quotes:
                report.errors.append(
                    f"NO MARK {symbol}: no quote this cycle, so its stop and "
                    "target were not checked. The position is still open."
                )
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
                    self._start_cooldown(signal.symbol, moment)
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

    def _venue_mode(self, ack: Any) -> str:
        """Which side of the house filled this — "paper" or "live".

        Stamped onto the booked position and carried into `closed_trades`,
        where `LiveTradingGate._graded_count` counts only rows reading "paper".
        So this is not a label: it decides whether a round trip advances the
        fifty that gate real money.

        Unknown resolves to LIVE, matching `trading.live_gate._is_live_venue`
        and `ThesisPipeline._is_paper`. The opposite default is the dangerous
        one — a fill nobody can attribute, counted as paper, pads the very bar
        that exists to hold live trading back.
        """
        name = getattr(ack, "venue", None) if ack is not None else None
        for adapter in getattr(self.router, "adapters", []):
            if adapter.name == name:
                return self.router.mode_of(adapter)
        return "live"

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
        record = self.position_book.close(
            symbol, fill if fill is not None else planned_price, reason=reason,
            planned_exit=planned_price,
            exit_fill_source="venue" if fill is not None else "mid",
        )
        if record:
            self._announce({
                "symbol": symbol, "side": "sell",
                "quantity": record.get("quantity"),
                "price": record.get("exit_price"),
                "mode": record.get("mode"), "venue": record.get("venue"),
                "reason": record.get("reason"),
                "realized_usd": record.get("realized_usd"),
            })
        return record

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
        crypto = [h for h in holdings
                  if h.asset_class == "crypto" and h.quantity > 0]
        marks = await self._quote_map([h.symbol for h in crypto])
        for holding in crypto:
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
                    # The MARK, not the fill. Passing the fill made
                    # `planned_exit == exit_price` on a venue-priced row, so it
                    # passed `_bridge`'s filter and contributed a perfectly
                    # plausible $0.00 of trading cost — the exact fiction that
                    # bridge exists to expose. And `or 0.0` booked a close at
                    # zero, i.e. a -100% realised return, on any filled ack the
                    # venue did not price.
                    mark = marks.get(holding.symbol)
                    if mark is None:
                        report.errors.append(
                            f"FLATTEN UNPRICED {holding.symbol}: sold at the "
                            "venue but no mark was available to price the "
                            "close against; the book still holds it"
                        )
                        continue
                    self._start_cooldown(holding.symbol, moment)
                    self._book_close(
                        holding.symbol, ack, planned_price=mark, reason="flatten",
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

    def _universe_for(self, session: Session, moment: datetime) -> list[str]:
        """Configured watchlist if one exists, otherwise the scout screens the
        whole tape. A configured list is an override, not the normal path."""
        # Nothing is bought during the weekend handoff. `should_flatten_crypto`
        # is selling the crypto book to free capital for the open, and the
        # session is still CRYPTO_ONLY until 04:00 — so without this the same
        # cycle sold BTC, deliberated BTC and bought it back, paying the spread
        # twice (187bps, measured) and manufacturing a round trip out of an
        # accounting event.
        if should_flatten_crypto(moment):
            return []

        if not session.equities_open:
            names = list(self.crypto_watchlist)
        elif self.equity_watchlist:
            names = list(self.equity_watchlist)
        else:
            names = None

        if names is not None:
            return self._not_cooling_off(names, moment)
        if self.scout is None:
            return []
        try:
            return self._not_cooling_off(
                [c.symbol for c in self.scout.scan(
                    limit=self.max_candidates_per_cycle * 2)], moment)
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


def _trading_day(moment: datetime):
    """The session a moment belongs to, for the cooldown ledger."""
    return moment.astimezone(EASTERN).date()
