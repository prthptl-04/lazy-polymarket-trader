"""Compose the fund stack from config.

`dashboard/__main__.py` used to build only the Polymarket runtime, so the fund
existed but nothing constructed one. This assembles it:

    config → venue → router (+ PDT + kill-switch) → pipeline
           → round table → data provider → FundLoop → FundScheduler

It returns `None` rather than raising whenever the fund cannot be built —
no watchlist, no Anthropic key, no data provider. A dashboard that refused to
start because the fund was unconfigured would be worse than one that starts
and says the fund is not attached, which is exactly what the UI shows.

Paper mode is the default and is not negotiable here: the venue is a
`PaperVenue` unless a real adapter is passed in. Flipping to live is the rule
#13 checklist, not a config key.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from cache.gemini_backend import GeminiBackend
from cache.cost_ledger import CostLedger
from cache.llm_router import LlmRouter
from roundtable.corroborator import Corroborator
from roundtable.engine import RoundTable
from roundtable.postmortem import Postmortem
from trading.fund import FundLoop
from trading.fund_config import FundConfig, load_config
from trading.fund_scheduler import FundScheduler
from trading.kill_switch import DailyLossKillSwitch
from trading.live_gate import LiveTradingGate
from trading.market_data import StaticProvider, VenueQuoteProvider
from trading.massive_provider import MassiveProvider
from trading.position_book import PositionBook
from trading.discovery import MarketScout
from trading.sec_edgar import SecEdgarFundamentals
from trading.pdt import DayTradeTracker
from trading.pipeline import CONFIDENCE_SHRINK, ThesisPipeline
from trading.mcp_client import McpSession
from trading.venues.paper import PaperVenue
from trading.venues.robinhood import MCP_URL, RobinhoodVenue
from trading.venues.router import VenueRouter
from verification.criteria import DEFAULT_CRITERIA, VerifiedOutcomeCriteria
from verification.outcome_grader import OutcomeGrader

logger = logging.getLogger(__name__)


def build_data_provider(config: FundConfig, venue: Any) -> Optional[Any]:
    """Resolve the configured provider.

    `none` yields a quote-only provider: live prices from the venue, no history
    and no fundamentals. The fund will pre-screen every candidate out for
    having no exit plan, which is the honest outcome of having no price
    history — and it is visible in the cycle report rather than silent.
    """
    provider = config.data_provider
    if provider == "static":
        return StaticProvider()
    if provider == "massive":
        # Bars from Massive; fundamentals from SEC EDGAR, because Massive's
        # plan returns NOT_ENTITLED for financial statements.
        return MassiveProvider(financials=SecEdgarFundamentals())
    if provider in ("", "none"):
        return VenueQuoteProvider(adapter=venue)
    logger.warning(
        "unknown data provider %r; falling back to quotes-only", provider
    )
    return VenueQuoteProvider(adapter=venue)


def build_fund(
    *,
    config: Optional[FundConfig] = None,
    memory: Any = None,
    anthropic_client: Any = None,
    venue: Any = None,
    criteria: VerifiedOutcomeCriteria = DEFAULT_CRITERIA,
) -> Optional[FundScheduler]:
    """Assemble the fund. Returns None when it cannot be built."""
    cfg = config or load_config()

    for warning in cfg.warnings:
        logger.warning("fund config: %s", warning)

    # An empty watchlist is now the normal case: the scout finds candidates.
    scout = MarketScout(limit=cfg.max_candidates_per_cycle * 2) if not cfg.equity_watchlist else None
    if not cfg.is_tradable and scout is None:
        logger.info("fund not attached: no watchlist and no scout")
        return None

    client = anthropic_client or _default_client()
    if client is None:
        logger.info("fund not attached: no Anthropic client (ANTHROPIC_API_KEY unset)")
        return None

    # Gemini failover. Absent config it reports unavailable and the router
    # simply never routes to it.
    gemini = GeminiBackend()
    # Named distinctly: an earlier version called both of these `router`,
    # so the venue router silently replaced the LLM router and the round
    # table was handed the wrong object entirely.
    # The ledger asks the venue router which mode is live at record time, so a
    # flip is picked up by the next model call without threading an argument
    # through the round table.
    cost_ledger = CostLedger(memory=memory)
    llm_router = LlmRouter(client=client, gemini=gemini, ledger=cost_ledger)
    if not gemini.available:
        logger.info("Gemini failover not configured (no GEMINI_API_KEY, no CLI)")

    # Robinhood supplies REAL prices; fills stay simulated. That is what makes
    # the 50 paper trades rule #13 requires actually meaningful — a paper record
    # built on synthetic prices proves nothing about live behaviour.
    #
    # Execution deliberately stays on PaperVenue. The live gate would refuse a
    # real venue anyway, but routing to paper means we are not relying on a gate
    # to avoid spending money; the order simply has nowhere real to go.
    quote_source, robinhood = _robinhood_quotes(memory)

    trading_venue = venue or PaperVenue(
        name="paper",
        starting_cash_usd=cfg.bankroll_usd,
        supported=("equity", "crypto"),
        quote_source=quote_source,
    )

    data_provider = build_data_provider(cfg, trading_venue)

    kill_switch = DailyLossKillSwitch(max_daily_loss_usd=cfg.max_daily_loss_usd)
    pdt = DayTradeTracker(account_equity_usd=cfg.account_equity_usd)
    # Rule #13 in the fund's own path. Without this a live venue would trade
    # for real with PAPER_TRADING=true having no effect at all.
    live_gate = LiveTradingGate(memory=memory, criteria=criteria,
                                bankroll_usd=cfg.bankroll_usd)
    # The live venue is REGISTERED, not selected. Registration is what makes the
    # dashboard's live switch real; selection still requires the operator to
    # switch live on AND rule #13's checklist to pass, and VenueRouter.venue_for
    # checks both on every order. With the checklist unmet — which is the state
    # today — every order still routes to paper.
    adapters = [trading_venue]
    if robinhood is not None and robinhood is not trading_venue:
        adapters.append(robinhood)
        logger.info("Robinhood registered as a LIVE venue. Execution stays on "
                    "paper until the rule-#13 checklist passes AND live is "
                    "switched on for it.")

    router = VenueRouter(adapters=adapters, pdt=pdt,
                         kill_switch=kill_switch, live_gate=live_gate)
    cost_ledger.mode_provider = lambda: (
        "live" if any(router.mode_of(a) == "live" and router._live_is_permitted(a)
                      for a in router.adapters)
        else "paper"
    )
    # Live starts OFF. A venue that arms itself the moment it is plugged in is
    # not a switch, and the operator should be the one to turn it on.
    router.set_mode_enabled("robinhood", "live", False)

    # Close the calibration loop. The shrink maps stated confidence onto a
    # probability before Kelly sizes anything; until now it was a pessimistic
    # constant and the fitted value was computed for display only, so the
    # dashboard described a feedback loop that was connected at neither end.
    #
    # Fitted ONCE at build, not per trade: a shrink that moves mid-run makes two
    # trades in the same cycle size differently for reasons unrelated to either
    # thesis. The clamp in fit_confidence_shrink still bounds it.
    shrink = _fit_shrink(memory)
    pipeline = ThesisPipeline(
        router=router,
        grader=OutcomeGrader(criteria),
        criteria=criteria,
        bankroll_usd=cfg.bankroll_usd,
        memory=memory,
        confidence_shrink=shrink.shrink if shrink.usable else CONFIDENCE_SHRINK,
    )
    logger.info("confidence shrink: %s", shrink.reason)

    # Blocker #2: without this the fund opens positions whose stops are never
    # checked. FundLoop only enforces exits when a position_book is attached.
    position_book = PositionBook(memory=memory)

    # The second fact set. Robinhood's quote source is genuinely independent of
    # the Massive-backed data provider, which is what makes the comparison worth
    # anything — corroborating a provider against itself would agree every time.
    corroborator = (
        Corroborator(primary=data_provider, secondary=robinhood)
        if robinhood is not None else None
    )
    if corroborator is None:
        logger.info("no secondary quote source; the Corroborator seat will be "
                    "told every figure is single-sourced")

    fund = FundLoop(
        router=router,
        position_book=position_book,
        cost_ledger=cost_ledger,
        pipeline=pipeline,
        round_table=RoundTable(client=client, router=llm_router, memory=memory),
        data=data_provider,
        corroborator=corroborator,
        equity_watchlist=cfg.equity_watchlist,
        crypto_watchlist=cfg.crypto_watchlist,
        kill_switch=kill_switch,
        scout=scout,
        memory=memory,
        postmortem=Postmortem(memory=memory),
        lookback_bars=cfg.lookback_bars,
        max_candidates_per_cycle=cfg.max_candidates_per_cycle,
        resume_max_age_seconds=cfg.resume_max_age_seconds,
    )

    scheduler = FundScheduler(
        fund=fund,
        venue=trading_venue,
        cycle_interval_seconds=cfg.cycle_interval_seconds,
    )
    # Exposed so the dashboard can show open positions and their live stops.
    scheduler.position_book = position_book
    scheduler.llm_router = llm_router
    scheduler.robinhood = robinhood
    return scheduler


def _fit_shrink(memory: Any):
    """Fit the confidence→probability shrink from resolved outcomes.

    Refuses by default: any failure, or too few rows, returns an unusable fit
    and the pessimistic constant stands. Sizing must never be loosened by an
    exception.
    """
    from roundtable.calibration import ShrinkFit, fit_confidence_shrink
    try:
        return fit_confidence_shrink(memory.resolved_outcomes(limit=1000))
    except Exception:
        logger.exception("could not fit the confidence shrink; keeping the constant")
        return ShrinkFit(None, 0, None, None, "fit failed; pessimistic constant stands")


def _robinhood_quotes(memory: Any) -> tuple[Optional[Any], Optional[Any]]:
    """Live quotes from Robinhood, if the daemon holds an authenticated session.

    Returns (quote_source, venue). Both None when unauthenticated — the fund
    then falls back to the data provider's prices and says so, rather than
    failing to start.
    """
    session = McpSession(server_url=MCP_URL)
    if not session.auth_summary().get("authenticated"):
        logger.info("Robinhood not authenticated; run scripts_mcp_auth.py once "
                    "for live quotes. Falling back to provider prices.")
        return None, None
    rh = RobinhoodVenue(session=session)
    return rh.get_quote, rh


def _default_client() -> Optional[Any]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception:
        logger.exception("could not construct the Anthropic client")
        return None
