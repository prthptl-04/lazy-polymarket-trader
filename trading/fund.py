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

from finance.exits import ExitPlan
from roundtable.knowledge import SourceRef
from roundtable.postmortem import Postmortem, relevant_lesson_lines
# One horizon, one definition. An order outlives its thesis at the moment
# that thesis gets scored against the tape — see _expire_stale_orders.
from roundtable.shadow import DEFAULT_HORIZON_HOURS as SHADOW_HORIZON_HOURS
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
    # Past deliberations resolved against the tape this cycle.
    scored_calls: int = 0
    expired_orders: list[str] = field(default_factory=list)
    prescreened_out: list[dict] = field(default_factory=list)
    deliberated: list[str] = field(default_factory=list)
    results: list[Any] = field(default_factory=list)     # PipelineResult
    flattened: list[str] = field(default_factory=list)
    exits: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Things worth SAYING that are not things going wrong. The scheduler counts
    # `errors` and the hourly health check reads that count, so a cycle that
    # declined to trade for a planned reason must not land there — an error
    # counter that ticks twelve times an hour during correct operation is an
    # error counter nobody reads.
    notes: list[str] = field(default_factory=list)
    halted_reason: Optional[str] = None

    @property
    def submitted(self) -> list[Any]:
        return [r for r in self.results if getattr(r, "submitted", False)]

    def summary(self) -> dict:
        return {
            "moment": self.moment.isoformat(),
            "session": self.session,
            "confidence_shrink": self.confidence_shrink,
            "scored_calls": self.scored_calls,
            "expired_orders": list(self.expired_orders),
            "universe": len(self.universe),
            "prescreened_out": len(self.prescreened_out),
            "deliberated": len(self.deliberated),
            # The NAMES, not just the counts. A universe of 12 screened from 58
            # is a different object from a hand-typed pair, and "12" on its own
            # says nothing about which 12 or why the rest were dropped.
            "universe_names": list(self.universe),
            "prescreen_rejections": [
                {"symbol": r.get("symbol"), "reason": r.get("reason")}
                for r in self.prescreened_out
            ][:20],
            "deliberated_names": list(self.deliberated),
            "submitted": len(self.submitted),
            "flattened": len(self.flattened),
            "exits": len(self.exits),
            "errors": len(self.errors),
            "notes": list(self.notes),
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
    # client_order_id -> the exit plan a RESTING entry was sized against, held
    # until the order fills. Persisted with the rest of the fund's state: an
    # order outlives the process, so its plan has to as well.
    _pending_plans: dict = field(default_factory=dict)
    cost_ledger: Any = None                  # cache.cost_ledger.CostLedger
    corroborator: Any = None                 # roundtable.corroborator.Corroborator
    scout: Any = None
    # trading.crypto_discovery.CryptoScout — the weekend equivalent of
    # `scout`. None falls back to the configured watchlist.
    crypto_scout: Any = None
    # Venue exposing `currency_pairs()`. Awaited in the cycle's own loop —
    # the MCP session cannot be used from another one.
    pair_source: Any = None
    # trading.social_sentiment.SocialSentimentFeed. Optional.
    social: Any = None
    # roundtable.shadow.ShadowResolver — scores past deliberations against
    # the tape so the seats calibrate without waiting on positions.
    shadow: Any = None
    memory: Any = None
    postmortem: Any = None
    # trading.catalysts.CatalystFeed. Optional: None disables the
    # Catalyst seat's evidence without disabling the seat, which then
    # correctly reports that it has nothing to reason from.
    catalysts: Any = None
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
        await self._reconcile_resting(report)
        self._reconcile_cash(report)

        self._recalibrate()
        report.confidence_shrink = getattr(self.pipeline, "confidence_shrink", None)

        # 1. Show the mark to the kill-switch BEFORE anything is decided.
        if self.kill_switch is not None and equity_usd is not None:
            # TRADING equity. The daily loss limit exists to stop a strategy
            # that is losing money today; an amount booked to a venue's
            # suspense account is a known accounting artifact with a stated
            # reason, and it is not a trade.
            #
            # Measured 2026-09-22 05:37: daily_pnl -$46.33 of a $50 limit, of
            # which -$43.75 was the booked gap and only -$2.58 was trading. The
            # Risk Manager was reading $3.67 of headroom and vetoing every
            # equity entry on it — correctly, given what it was shown. 39 of 39
            # opinions bearish, 28 of 29 theses neutral, nothing submitted for
            # five cycles. A bookkeeping hole had switched the fund off.
            #
            # This narrows the INPUT to what the limit always claimed to
            # measure. The $50 limit is unchanged, and an unbooked gap still
            # counts in full: `absorb_gap` is never called automatically and
            # requires a stated reason, so nothing is excluded until a human
            # has looked at it and said what it was.
            self.kill_switch.observe_equity(
                moment, equity_usd - self._booked_suspense())

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
        # Score what the committee already said, before it says anything new.
        # Runs first so this cycle's seat weights reflect every call resolved
        # since the last one — including calls that never became positions.
        # Refresh the tradable pair list HERE, on the cycle's loop. The scout
        # is sync and cannot await, and doing this from a worker thread's own
        # event loop is what silently emptied the crypto universe.
        await self._refresh_crypto_pairs()

        await self._score_past_calls(report)

        # BEFORE the working-order guard, because the guard is what makes a
        # stale order permanent: it skips any name with an order outstanding,
        # so an order that never fills and never leaves locks that name out of
        # every future cycle.
        await self._expire_stale_orders(report)

        universe = self._universe_for(session, moment)
        # A name with an order already working is not a new opportunity.
        universe, working = self._drop_working(universe)
        report.prescreened_out.extend(working)
        report.universe = list(universe)
        if not universe:
            # A cycle that reports nothing at all is indistinguishable from a
            # quiet market. Say which it is — an unconfigured weekend is most
            # of the week under rule #23, and it used to be silent.
            if (not session.equities_open and not self.crypto_watchlist
                    and self.crypto_scout is None):
                report.errors.append(
                    "no crypto watchlist configured and no crypto scout "
                    "attached, so there is nothing to trade while equities are "
                    "shut. Either set FUND_CRYPTO_WATCHLIST or attach "
                    "trading.crypto_discovery.CryptoScout, which screens every "
                    "tradable Robinhood pair."
                )
            elif should_flatten_crypto(moment):
                report.notes.append(
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
        asset_class = self._asset_class_of(symbol)

        history = await self.data.get_history(symbol, lookback=self.lookback_bars)
        quote = await self.data.get_quote(symbol)
        quote_at = time.time()
        price = (quote.mid if quote else None) or (
            history.closes[-1] if history and history.closes else None
        )
        if history is None or price is None:
            report.prescreened_out.append(
                {"symbol": symbol, "reason": "no price data available"}
            )
            return False

        news_at = time.time()
        sentiment_notes = await self._news_notes(symbol)
        sentiment_notes = sentiment_notes + await self._social_notes(symbol)
        catalysts_at = time.time()
        catalyst_notes = await self._catalyst_notes(
            symbol, asset_class, spot=price, session=session,
            stop_distance_pct=_stop_distance_pct(history.bars, price))
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
            catalyst_notes=catalyst_notes,
            corroboration_notes=corroboration_notes,
            portfolio_notes=self._portfolio_notes(symbol, held, moment),
            lessons=relevant_lesson_lines(
                self.memory, asset_class=asset_class, symbol=symbol),
            # Where each block came from and when it was true. Recorded at the
            # point of fetch — anywhere later and the timestamp would be the
            # time we got round to writing it down, not the time it was true.
            sources=self._sources(quote_at, news_at, catalysts_at),
            budget_notes=self._budget_notes(),
            regime_notes=self._regime_notes(),
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
        # A RESTING entry has not traded, so there is nothing to book yet — but
        # the plan it was sized against must survive the cycle, or the fill
        # arrives later with no stop and no way back to the book. That is how a
        # filled resting order became a position the book never knew about, and
        # `fund_state._venue_rows` seeds the venue from the BOOK, so the next
        # restart destroyed it while its cash stayed spent.
        if (self.position_book is not None and result.submitted
                and not result.filled and result.trade is not None
                and result.trade.is_entry and result.size is not None
                and result.ack is not None and result.ack.client_order_id):
            self._pending_plans[str(result.ack.client_order_id)] = {
                "symbol": symbol,
                "asset_class": asset_class,
                "plan": {"entry": built.exit_plan.entry,
                         "stop": built.exit_plan.stop,
                         "target": built.exit_plan.target,
                         "direction": built.exit_plan.direction,
                         "atr": built.exit_plan.atr},
                "planned_entry": built.exit_plan.entry,
                "spread_bps_at_entry": built.candidate.spread_bps,
                "thesis_id": thesis.thesis_id,
                "signal": thesis.consensus.signal if thesis.consensus else None,
                "confidence": (thesis.consensus.confidence
                               if thesis.consensus else None),
                "venue": result.ack.venue,
                "mode": self._venue_mode(result.ack),
            }

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

    async def _reconcile_resting(self, report: CycleReport) -> None:
        """Book any resting order the market reached since the last cycle.

        REFRESHES THE QUOTE FIRST, and that is the whole point. `match_resting`
        compares the limit against the venue's quote CACHE, which is populated
        by `get_quote`. Without a refresh, every resting order was matched
        against the quote captured at placement — which by definition did not
        fill it — so a resting order could never fill. Not rarely: never,
        whatever the market did. Twelve orders, zero fills, including one
        sitting 0.9% through the touch.

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
            # One refresh per symbol, on the cycle's own loop. A dead quote
            # source must not leave every OTHER resting order unmatched.
            for symbol in {getattr(r, "symbol", None)
                           for r in (getattr(adapter, "_resting", {}) or {}).values()}:
                if not symbol:
                    continue
                try:
                    await adapter.get_quote(symbol)
                except Exception:
                    logger.debug("could not refresh %s before matching",
                                 symbol, exc_info=True)
            try:
                for ack in match():
                    # A fill is the system working. It belongs in the record,
                    # not in the error count.
                    report.notes.append(
                        f"RESTING FILL {ack.raw.get('symbol', '?')}: an order "
                        f"placed on an earlier cycle filled at "
                        f"{ack.raw.get('fill_price')}."
                    )
                    self._book_resting_fill(ack, report)
            except Exception:
                logger.exception("could not reconcile resting orders")

    def _book_resting_fill(self, ack: Any, report: CycleReport) -> None:
        """Register a resting fill in the book, with the plan it was sized on.

        Two things go wrong without this, and the second is worse than the
        first. The position is not persisted — `fund_state._venue_rows` seeds
        the venue from the BOOK, so a position the book never learned about is
        destroyed on the next restart while the cash that bought it is not.
        And nothing watches its stop: `_process_exits` walks the book, so an
        unbooked position rides straight through the level the thesis chose.

        A fill whose plan is gone is NOT booked with an invented stop. A
        fabricated exit level is worse than a missing one, because it looks
        like a decision somebody made. It is reported as a real error instead.
        """
        if self.position_book is None or ack is None:
            return
        raw = getattr(ack, "raw", None) or {}
        symbol = raw.get("symbol")
        quantity = raw.get("quantity")
        price = raw.get("fill_price")
        pending = self._pending_plans.pop(str(getattr(ack, "client_order_id", "")), None)
        if pending is None:
            report.errors.append(
                f"{symbol} filled from a resting order with no stored exit "
                f"plan, so it is held at the venue but NOT in the book: its "
                f"stop is unwatched and a restart would discard it while the "
                f"cash stays spent. Close it by hand or restore its plan."
            )
            return
        if not symbol or not quantity or not price:
            report.errors.append(
                f"a resting fill arrived without symbol, quantity or price "
                f"({raw!r}); it cannot be booked")
            return
        try:
            self.position_book.open(
                symbol=symbol,
                asset_class=pending.get("asset_class", "crypto"),
                quantity=float(quantity),
                entry_price=float(price),
                plan=ExitPlan(**pending["plan"]),
                thesis_id=pending.get("thesis_id"),
                signal=pending.get("signal"),
                confidence=pending.get("confidence"),
                venue=pending.get("venue"),
                mode=pending.get("mode"),
                planned_entry=pending.get("planned_entry"),
                entry_fill_source="venue",
                spread_bps_at_entry=pending.get("spread_bps_at_entry"),
            )
        except Exception:
            logger.exception("could not book the resting fill for %s", symbol)
            report.errors.append(
                f"{symbol} filled but could not be booked; it is held at the "
                f"venue with an unwatched stop")

    def _booked_suspense(self) -> float:
        """Total booked to venue suspense accounts. Negative for a shortfall.

        NOT netted out of the bankroll the sizer uses: that cash genuinely is
        not there, and sizing against money the account does not hold is how a
        paper record stops predicting a live one. The asymmetry is deliberate —
        conservative where it decides how much to risk, accurate where it
        decides whether today has been a losing day.
        """
        total = 0.0
        for adapter in getattr(getattr(self, "router", None), "adapters", []) or []:
            try:
                total += float(getattr(adapter, "unexplained_usd", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
        return total

    def _reconcile_cash(self, report: CycleReport) -> None:
        """Ask every venue that can prove its books to prove them.

        A REAL error, not a note: money that moved without inventory moving
        with it is the one thing in a trading system that must never be
        absorbed quietly. It went unnoticed for hours precisely because
        nothing asked.

        The gap is reported, never repaired — a reconciliation that adjusts
        the cash to match the book is not a reconciliation.
        """
        for adapter in getattr(getattr(self, "router", None), "adapters", []) or []:
            check = getattr(adapter, "reconcile", None)
            if check is None:
                continue
            try:
                gap = check()
            except Exception:
                logger.exception("could not reconcile %s",
                                 getattr(adapter, "name", adapter))
                continue
            if gap:
                report.errors.append(
                    f"{getattr(adapter, 'name', 'venue')} does not reconcile: {gap}")

    def _resting_symbols(self) -> set[str]:
        """Symbols with an order already working at some venue.

        Read from the adapter's own resting book rather than tracked here, so
        it cannot drift from the truth. A live adapter has no local book — its
        orders live at the broker — and that degrades to "nothing known
        resting" rather than raising.
        """
        out: set[str] = set()
        for adapter in getattr(getattr(self, "router", None), "adapters", []) or []:
            resting = getattr(adapter, "_resting", None)
            if not resting:
                continue
            for request in resting.values():
                symbol = getattr(request, "symbol", None)
                if symbol:
                    out.add(str(symbol).upper())
        return out

    async def _expire_stale_orders(self, report: CycleReport) -> None:
        """Cancel resting orders that have outlived the thesis behind them.

        The horizon is the SHADOW horizon, not a number of its own: that is the
        point at which the call gets scored against the tape. Past it, the
        deliberation is a resolved prediction and the order is the only thing
        still acting on it.

        Never raises. A failure to tidy must not become a failure to trade.
        """
        for adapter in getattr(getattr(self, "router", None), "adapters", []) or []:
            expire = getattr(adapter, "expire_resting", None)
            if expire is None:
                continue
            try:
                cancelled = await expire(SHADOW_HORIZON_HOURS * 3600.0)
            except Exception:
                logger.exception("could not expire stale orders at %s",
                                 getattr(adapter, "name", adapter))
                continue
            if cancelled:
                report.expired_orders.extend(cancelled)
                logger.info("cancelled %d order(s) that outlived their thesis: %s",
                            len(cancelled), ", ".join(cancelled))

    def _drop_working(self, names: list[str]) -> tuple[list[str], list[dict]]:
        """Skip names that already have an order working.

        Not an untidiness — a risk. These orders rest BELOW the touch waiting
        for the market to come to them, so they do not fill one at a time, they
        fill together on the first dip that reaches the price. Six orders of
        $38 is $228 of a $500 account arriving at once in a name the sizer
        approved at $38.

        No existing guard covers this. `_not_cooling_off` bars a symbol only
        after a position CLOSES, the position book tracks positions rather than
        orders, and a resting order is by definition not a position yet — the
        gap is exactly the window between placing and filling, which on this
        venue is where orders live.

        Not a cooldown: the block lasts only while the order is working. Once it
        fills or is cancelled the name is tradable again.
        """
        working = self._resting_symbols()
        if not working:
            return list(names), []
        kept, skipped = [], []
        for name in names:
            if str(name).upper() in working:
                skipped.append({
                    "symbol": name,
                    "reason": "an order is already resting on this symbol; a "
                              "second would fill alongside the first rather "
                              "than instead of it",
                })
            else:
                kept.append(name)
        return kept, skipped

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

    @staticmethod
    def _sources(quote_at: Optional[float], news_at: float,
                 catalysts_at: float) -> tuple[SourceRef, ...]:
        """Provenance for the evidence block.

        Technicals and the exit plan are marked `derived`: they are computed
        from the bars in this same cycle, so they carry no independent age and
        a second staleness warning on them would train the seats to ignore the
        first one.
        """
        return (
            SourceRef("prices", "venue quote + bars", quote_at),
            SourceRef("news", "market-data news feed", news_at),
            SourceRef("catalysts", "OpenBB (headlines, SEC Form 4)", catalysts_at),
            SourceRef("technicals", "computed", None, derived=True),
        )

    async def _catalyst_notes(self, symbol: str, asset_class: str, *,
                              spot: Optional[float] = None,
                              stop_distance_pct: Optional[float] = None,
                              session: Any = None) -> tuple[str, ...]:
        """Dated events and insider flow, for the Catalyst seat.

        Optional by construction. `catalysts` never raises — it returns an
        explicit NOT AVAILABLE line — so an absent OpenBB install, a gated
        provider or a slow endpoint costs this cycle nothing but one evidence
        line saying so. A missing catalyst block must never be the reason a
        candidate is not debated.
        """
        if self.catalysts is None:
            return ()
        try:
            evidence = await self.catalysts.catalysts(
                symbol, asset_class, spot=spot,
                # The exit plan itself is built inside `build_candidate`, after
                # this — but the geometry is fixed (2xATR), so the distance the
                # fund WILL use is knowable now from the same bars. Computing
                # it here beats reordering the cycle for one line, and without
                # it the implied-move comparison has nothing to compare to.
                stop_distance_pct=stop_distance_pct,
                equities_open=bool(getattr(session, "equities_open", True)))
        except Exception:
            logger.exception("catalyst feed failed for %s", symbol)
            return ()
        return evidence.as_notes()

    async def _social_notes(self, symbol: str) -> tuple[str, ...]:
        """Retail chatter, for the Sentiment seat.

        Joins the SENTIMENT block rather than CATALYSTS: a forum post is mood
        and positioning, which is that seat's mandate, while the Catalyst seat
        owns dated facts. Optional and silent when unconfigured — a missing
        social feed must never be the reason a candidate is not debated.
        """
        if self.social is None:
            return ()
        try:
            pulse = await self.social.pulse_for(symbol)
        except Exception:
            logger.exception("social feed failed for %s", symbol)
            return ()
        return pulse.notes if pulse.available else ()

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
        """Turn a closed exit into lessons the seats read next time.

        Wins reach `Postmortem.analyse` too. It still records a finding only
        where arithmetic establishes one — on a win that is underconfidence,
        the mirror of the overconfidence fault already recorded on losses.
        Filtering wins out HERE meant that half of the calibration evidence
        never reached the analyser that knows what to do with it."""
        if self.postmortem is None or not record:
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
                # Scopes the lesson to the market it was learned in, so a
                # weekend crypto finding is never read as equity evidence.
                asset_class=record.get("asset_class"),
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

    def _asset_class_of(self, symbol: str) -> str:
        return classify_asset_class(symbol, self.crypto_watchlist)

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

        # The crypto branch RETURNS on every path. It used to fall through
        # when no crypto scout was attached, and control continued past the
        # elif chain into the equity screen — so a crypto_only session at 22:10
        # debated META, MU, QCOM and GOOGL. A session boundary has to hold
        # whatever is or is not attached, so nothing here may reach the code
        # below.
        if not session.equities_open:
            # A configured crypto list is an override, exactly as on the equity
            # side. Left empty, the crypto scout screens the whole tradable
            # market instead of the two names somebody once typed.
            configured = list(self.crypto_watchlist)
            if configured:
                return self._not_cooling_off(configured, moment)
            if self.crypto_scout is None:
                return []
            try:
                return self._not_cooling_off(
                    [c.symbol for c in self.crypto_scout.scan(
                        limit=self.max_candidates_per_cycle * 2)], moment)
            except Exception:
                logger.exception("crypto scout scan failed")
                return []

        if self.equity_watchlist:
            return self._not_cooling_off(list(self.equity_watchlist), moment)

        if self.scout is None:
            return []
        try:
            return self._not_cooling_off(
                [c.symbol for c in self.scout.scan(
                    limit=self.max_candidates_per_cycle * 2)], moment)
        except Exception as e:
            logger.exception("scout scan failed")
            return []

    async def _refresh_crypto_pairs(self) -> None:
        """Hand the scout the pairs this account can actually trade.

        Never raises: a pair list that cannot be refreshed leaves the previous
        one standing, and a failure to learn what is tradable must not become a
        failure to run the cycle.
        """
        if self.crypto_scout is None or self.pair_source is None:
            return
        try:
            pairs = await self.pair_source.currency_pairs()
        except Exception:
            logger.exception("could not refresh the tradable crypto pairs")
            return
        self.crypto_scout.refresh_pairs(pairs or [])

    async def _score_past_calls(self, report: CycleReport) -> None:
        """Resolve due deliberations against the tape. Never raises — a failure
        to learn must not become a failure to trade.

        ASYNC, and the prices are fetched here rather than inside the resolver.
        The resolver is sync, and the sync quote callable it used to be given
        reached an async venue — so it ran that coroutine on a worker thread's
        own loop and blocked THIS loop waiting for it. The venue's MCP session
        belongs to this loop, so it never finished: no cycles and no dashboard
        until the process was killed. `_refresh_crypto_pairs` above learned the
        same lesson; awaiting on the loop that owns the session is the fix in
        both places."""
        if self.shadow is None:
            return
        try:
            symbols = self.shadow.due_symbols()
            quotes = await self._quotes(symbols) if symbols else {}
            prices = {s: q.mid for s, q in quotes.items()
                      if getattr(q, "mid", None)}
            scored = self.shadow.resolve_due(prices=prices)
        except Exception:
            logger.exception("scoring past calls failed")
            return
        if scored:
            report.scored_calls = scored
            logger.info("scored %d past deliberation(s) against the tape", scored)

    def _regime_notes(self) -> tuple[str, ...]:
        """Whether the committee is exploring or exploiting.

        Read from the ROUTER's live gate rather than a config flag: the gate is
        what actually decides whether money can move, and a note that keyed off
        anything else could appear against a live account through a
        disagreement between two settings. Anything unknown resolves to LIVE,
        which yields no note — the same refuse-by-default stance the gate takes.
        """
        from trading.fund_config import cold_start_note
        from roundtable.calibration import MIN_SAMPLES_FOR_FIT
        try:
            gate = getattr(getattr(self, "router", None), "live_gate", None)
            paper = True if gate is None else not gate.status().get("live_possible", True)
        except Exception:
            logger.exception("could not read the live gate; assuming LIVE")
            return ()
        try:
            resolved = len(self.memory.resolved_outcomes(limit=MIN_SAMPLES_FOR_FIT))
        except Exception:
            return ()
        note = cold_start_note(paper=paper, resolved=resolved,
                               required=MIN_SAMPLES_FOR_FIT)
        return (note,) if note else ()

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


def _stop_distance_pct(bars: Sequence[Any], price: float) -> Optional[float]:
    """How far the 2xATR stop will sit from entry, as a percent.

    Duplicates nothing: it reads `DEFAULT_STOP_MULTIPLIER` and
    `average_true_range` from `finance.exits`, so if the geometry changes this
    follows it. Returns None rather than a guess when the bars cannot support
    an ATR — a fabricated stop distance would make the implied-move line read
    as a real comparison when it is not one.
    """
    from finance.exits import DEFAULT_STOP_MULTIPLIER, average_true_range
    if not bars or not price or price <= 0:
        return None
    try:
        atr = average_true_range(bars)
    except Exception:
        return None
    if not atr or atr <= 0:
        return None
    return round(atr * DEFAULT_STOP_MULTIPLIER / price * 100.0, 2)


def classify_asset_class(symbol: str,
                         crypto_watchlist: Sequence[str] = ()) -> str:
    """Crypto or equity, decided by the INSTRUMENT.

    This used to be `symbol in crypto_watchlist`, which broke the moment the
    crypto scout made an empty watchlist the normal case: every pair the scout
    found classified as an equity, so the fund asked SEC EDGAR for a token's
    balance sheet, attempted Altman Z on a coin, and convened the two
    equity-only seats for instruments they have no mandate over.

    Robinhood spells every crypto pair `BASE-USD`, and no US equity ticker
    contains a hyphen — so the symbol carries the answer and no configuration
    can drift out of step with it.

    The configured watchlist still counts, because the override path uses bare
    symbols (`FUND_CRYPTO_WATCHLIST=BTC,ETH`) that have no suffix to read.
    """
    name = (symbol or "").strip().upper()
    if name.endswith("-USD") or name.endswith("-USDC"):
        return "crypto"
    if name in {s.strip().upper() for s in crypto_watchlist or ()}:
        return "crypto"
    return "equity"


def _trading_day(moment: datetime):
    """The session a moment belongs to, for the cooldown ledger."""
    return moment.astimezone(EASTERN).date()
