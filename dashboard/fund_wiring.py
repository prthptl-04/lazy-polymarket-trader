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

from roundtable.engine import RoundTable
from trading.fund import FundLoop
from trading.fund_config import FundConfig, load_config
from trading.fund_scheduler import FundScheduler
from trading.kill_switch import DailyLossKillSwitch
from trading.market_data import StaticProvider, VenueQuoteProvider
from trading.pdt import DayTradeTracker
from trading.pipeline import ThesisPipeline
from trading.venues.paper import PaperVenue
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

    if not cfg.is_tradable:
        logger.info("fund not attached: watchlist is empty")
        return None

    client = anthropic_client or _default_client()
    if client is None:
        logger.info("fund not attached: no Anthropic client (ANTHROPIC_API_KEY unset)")
        return None

    trading_venue = venue or PaperVenue(
        name="paper",
        starting_cash_usd=cfg.bankroll_usd,
        supported=("equity", "crypto"),
    )

    kill_switch = DailyLossKillSwitch(max_daily_loss_usd=cfg.max_daily_loss_usd)
    pdt = DayTradeTracker(account_equity_usd=cfg.account_equity_usd)
    router = VenueRouter(adapters=[trading_venue], pdt=pdt, kill_switch=kill_switch)

    pipeline = ThesisPipeline(
        router=router,
        grader=OutcomeGrader(criteria),
        criteria=criteria,
        bankroll_usd=cfg.bankroll_usd,
        memory=memory,
    )

    fund = FundLoop(
        router=router,
        pipeline=pipeline,
        round_table=RoundTable(client=client, memory=memory),
        data=build_data_provider(cfg, trading_venue),
        equity_watchlist=cfg.equity_watchlist,
        crypto_watchlist=cfg.crypto_watchlist,
        kill_switch=kill_switch,
        memory=memory,
        lookback_bars=cfg.lookback_bars,
        max_candidates_per_cycle=cfg.max_candidates_per_cycle,
        resume_max_age_seconds=cfg.resume_max_age_seconds,
    )

    return FundScheduler(
        fund=fund,
        venue=trading_venue,
        cycle_interval_seconds=cfg.cycle_interval_seconds,
    )


def _default_client() -> Optional[Any]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception:
        logger.exception("could not construct the Anthropic client")
        return None
