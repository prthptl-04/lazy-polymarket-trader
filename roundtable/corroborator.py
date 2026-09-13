"""The Corroborator — fetches a second opinion on the facts.

Pairs with the Analyst. The Analyst reasons about a symbol; this independently
re-fetches the same quantities from a *different* source and, where the user
has allowed it, adds scraped context. `roundtable.corroboration.compare` then
says whether the two agree.

Sources are deliberately independent. Corroborating a Massive number against
Massive proves nothing — so the secondary must be a different provider (a
venue's own quote, a second vendor) or the check is theatre.

Scraping goes through `PlaywrightFetcher` — the only scraper in this codebase
that actually works (Agent Reach has no backends installed, Scrapling's import
fails), and it routes every URL through the rule-#8 trust gate before a browser
launches.

Scraped text is context for a seat to read, never a number fed into `compare`.
A figure lifted off a web page is not a corroborating source, it is a rumour
with a citation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from roundtable.corroboration import CorroborationReport, compare

logger = logging.getLogger(__name__)

MAX_SCRAPE_NOTES = 5


@dataclass
class Corroborator:
    """Builds a second fact set and compares it to the primary.

    `primary` and `secondary` are MarketDataProviders. `reach` is an optional
    `AgentReachFetcher` for narrative context.
    """

    primary: Any
    secondary: Any = None
    # PlaywrightFetcher. Named `browser` because that is what it is.
    browser: Any = None
    scrape_urls: tuple[str, ...] = ()

    async def run(self, symbol: str, *, scrape: bool = False) -> CorroborationReport:
        primary_facts = await self._facts(self.primary, symbol)
        notes: list[str] = []

        if self.secondary is None:
            report = compare(symbol, primary_facts, {},
                             notes=["No secondary provider configured — nothing corroborated."])
        else:
            secondary_facts = await self._facts(self.secondary, symbol)
            report = compare(symbol, primary_facts, secondary_facts)

        if scrape and self.browser is not None:
            notes.extend(await self._scrape_notes(symbol))
        report.notes.extend(notes)
        return report

    # ---------- fact extraction ----------

    async def _facts(self, provider: Any, symbol: str) -> dict[str, float]:
        """The quantities worth cross-checking. Everything is best-effort: a
        provider that cannot answer contributes nothing rather than raising."""
        out: dict[str, float] = {}
        try:
            quote = await provider.get_quote(symbol)
        except Exception:
            quote = None
        if quote is not None:
            price = quote.mid if quote.mid is not None else quote.last
            if price is not None:
                out["price"] = float(price)

        try:
            history = await provider.get_history(symbol, lookback=20)
        except Exception:
            history = None
        if history and history.closes:
            out["last_close"] = float(history.closes[-1])
            if history.volumes:
                out["volume"] = float(history.volumes[-1])
            # Bar count is an integrity check: two sources disagreeing on how
            # many trading days exist means one of them has gaps.
            out["bar_count"] = float(len(history.bars))
        return out

    # ---------- narrative ----------

    async def _scrape_notes(self, symbol: str) -> list[str]:
        """Render the configured pages. Blocking I/O, so off the event loop."""
        import asyncio

        notes: list[str] = []
        for template in self.scrape_urls[:MAX_SCRAPE_NOTES]:
            url = template.format(symbol=symbol.upper())
            try:
                page = await asyncio.to_thread(self.browser.fetch, url)
            except Exception as e:
                notes.append(f"{url}: fetch failed ({type(e).__name__})")
                continue
            if not page.approved:
                # The trust gate refused. That is a working gate, not an error.
                notes.append(f"{url}: blocked by the scrape gate — {page.error}")
                continue
            if not page.ok:
                # Rendered, but a 4xx/5xx body is an error page, not research.
                notes.append(f"{url}: unusable (status {page.status})")
                continue
            notes.append(f"{url} (unverified, narrative only): "
                         f"{' '.join((page.text or '').split())[:400]}")
        return notes
