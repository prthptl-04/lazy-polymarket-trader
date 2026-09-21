"""Catalysts — the dated events and insider flow that move a price on the day.

The fund reasons from price structure, balance sheets and scraped sentiment.
None of those see the two things that most often decide a week: an event on the
calendar, and the people who run the company buying or selling their own stock.
This supplies both, from OpenBB.

**Optional, always.** OpenBB is not imported at module load and is not required.
Most of its calendars need a provider API key, so a keyless install yields news
and Form 4 filings and says plainly that the rest is unavailable. Every failure
path lands on "no catalyst evidence", never on a raised exception: this is one
input among many and a cycle must not end because a news endpoint was down.

**Computed, not narrated.** `candidate_builder`'s rule applies here — everything
the table sees is derived before any seat is consulted. Handing six seats twenty
raw Form 4 rows would have each of them do the arithmetic independently, badly,
and differently. `summarise_insiders` does it once.

**Narrative never becomes a number.** Headlines are context. Bodies are dropped
entirely — they are thousands of tokens per candidate and are exactly where a
seat finds a figure nobody verified. This is the same stance
`roundtable.corroboration` takes toward scraped values, for the same reason.

Placement per CLAUDE.md #16: the calls are blocking network I/O, so they run in
a thread rather than on the event loop, and they are bounded by a timeout. Never
on a hot path — this runs while candidates are built, not while an order moves.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 12.0
MAX_HEADLINES = 8
# Form 4s older than this say more about last quarter than about this setup.
INSIDER_LOOKBACK_DAYS = 90


@dataclass(frozen=True)
class CatalystEvidence:
    """What the Catalyst seat is given. Empty is a valid, common answer."""

    notes: tuple[str, ...] = ()
    available: bool = False
    reason: str = ""
    # Which sources could not be reached, so the seat can discount rather than
    # assume silence means "nothing happening".
    degraded: tuple[str, ...] = ()

    def as_notes(self) -> tuple[str, ...]:
        if not self.available:
            return (f"CATALYSTS NOT AVAILABLE — {self.reason}",)
        out = list(self.notes)
        if self.degraded:
            out.append("Sources unavailable this cycle: " + "; ".join(self.degraded)
                       + ". Treat as unknown, not as absent.")
        return tuple(out)


@dataclass
class CatalystFeed:
    """Fetches catalyst evidence for one symbol."""

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    # Injected so tests never touch the network and never import OpenBB.
    _import: Optional[Callable[[], Any]] = None

    def _obb(self) -> Any:
        if self._import is not None:
            return self._import()
        from openbb import obb          # lazy: the fund runs without it
        return obb

    async def catalysts(self, symbol: str, asset_class: str = "equity") -> CatalystEvidence:
        try:
            obb = await asyncio.to_thread(self._obb)
        except ImportError as e:
            return CatalystEvidence(
                reason=f"OpenBB is not installed ({e}). Catalyst evidence is optional; "
                       "install `openbb` to enable it.")
        except Exception as e:
            logger.warning("could not load OpenBB: %s", e)
            return CatalystEvidence(reason=f"OpenBB failed to load: {type(e).__name__}")

        notes: list[str] = []
        degraded: list[str] = []

        headlines = await self._safe(obb, "news", symbol, degraded,
                                     lambda: obb.news.company(symbol=symbol,
                                                              limit=MAX_HEADLINES * 2,
                                                              provider="yfinance"))
        if headlines is not None:
            lines = summarise_news(headlines)
            notes.extend(lines or ("No company news returned for this symbol.",))

        # Crypto has no Form 4s and no issuer. Asking is a wasted call and a
        # misleading "unavailable" line in the evidence block.
        if asset_class == "equity":
            insiders = await self._safe(obb, "insider filings", symbol, degraded,
                                        lambda: obb.equity.ownership.insider_trading(
                                            symbol=symbol, limit=60, provider="sec"))
            if insiders is not None:
                notes.append(summarise_insiders(insiders))

        if not notes:
            return CatalystEvidence(
                reason="every catalyst source failed this cycle",
                degraded=tuple(degraded))
        return CatalystEvidence(notes=tuple(notes), available=True,
                                degraded=tuple(degraded))

    async def _safe(self, obb: Any, label: str, symbol: str,
                    degraded: list[str], call: Callable[[], Any]) -> Optional[list[dict]]:
        """One endpoint. A failure degrades that source and nothing else.

        Most OpenBB calendars require a paid provider key, so a gated endpoint
        is the NORMAL case on a keyless install — losing the whole block over
        one of them would leave the feed showing nothing at all.
        """
        try:
            result = await asyncio.wait_for(asyncio.to_thread(call),
                                            timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            # ponytail: the worker thread runs on to completion — Python cannot
            # cancel one. The cycle stops WAITING, which is the guarantee that
            # matters; the orphaned thread costs a socket until the provider
            # answers. Move to a subprocess only if a provider ever hangs long
            # enough for those to accumulate.
            degraded.append(f"{label} (timed out after {self.timeout_seconds:.0f}s)")
            return None
        except Exception as e:
            reason = str(e).strip() or type(e).__name__
            degraded.append(f"{label} ({reason[:80]})")
            return None
        return _rows(result)


# ---------------------------------------------------------------- derivation

def summarise_news(rows: list[dict]) -> tuple[str, ...]:
    """Headlines, dated and attributed. Bodies dropped.

    The date is not decoration: without it a seat cannot tell this morning's
    story from last year's, and an LLM will happily treat both as current.
    """
    out: list[str] = []
    for row in rows[:MAX_HEADLINES]:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        date = str(row.get("date") or "")[:10]
        source = str(row.get("source") or "unknown source").strip()
        out.append(f"[{date}] {title} ({source})")
    return tuple(out)


def summarise_insiders(rows: list[dict]) -> str:
    """Net Form 4 activity, as one derived line.

    Direction is taken from `acquisition_or_disposition`, never from the sign of
    a value — SEC rows carry positive share counts on both sides.

    10b5-1 sales are called out rather than counted as conviction. A scheduled
    plan sale is a calendar entry an executive set up months earlier; reading it
    as a bearish signal is how an insider panel starts lying to the committee.
    """
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=INSIDER_LOOKBACK_DAYS)
    bought_usd = sold_usd = 0.0
    buyers: set[str] = set()
    sellers: set[str] = set()
    planned = 0

    for row in rows:
        shares = _num(row.get("securities_transacted"))
        price = _num(row.get("transaction_price"))
        if shares is None or price is None or shares <= 0 or price <= 0:
            continue                      # a value we cannot compute is not a value
        when = _date(row.get("transaction_date") or row.get("filing_date"))
        if when is not None and when < cutoff:
            continue
        value = shares * price
        who = str(row.get("owner_name") or "unknown")
        side = str(row.get("acquisition_or_disposition") or "").upper()
        if str(row.get("footnote") or "").find("10b5-1") >= 0:
            planned += 1
        if side.startswith("A"):
            bought_usd += value
            buyers.add(who)
        elif side.startswith("D"):
            sold_usd += value
            sellers.add(who)

    if not buyers and not sellers:
        return (f"Insiders: no Form 4 activity in the last {INSIDER_LOOKBACK_DAYS} days "
                "(or none with a reported price).")

    net = bought_usd - sold_usd
    verb = "bought" if net > 0 else "sold"
    parts = [
        f"Insiders: {len(buyers)} insider(s) bought ${bought_usd:,.0f} and "
        f"{len(sellers)} sold ${sold_usd:,.0f} over {INSIDER_LOOKBACK_DAYS} days — "
        f"net {verb} ${abs(net):,.0f}."
    ]
    if planned:
        parts.append(f"{planned} of these were 10b5-1 scheduled transactions, "
                     "which carry no view.")
    if len(buyers) >= 3 and net > 0:
        parts.append("Three or more distinct buyers is cluster buying, which is the "
                     "one insider pattern with a real literature behind it.")
    return " ".join(parts)


# ---------------------------------------------------------------- helpers

def _rows(result: Any) -> list[dict]:
    """OpenBB returns an OBBject of pydantic rows; tests pass plain dicts."""
    items = getattr(result, "results", result) or []
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
        elif hasattr(item, "model_dump"):
            out.append(item.model_dump())
    return out


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "year") and not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def _demo() -> None:
    assert "no Form 4" in summarise_insiders([])
    assert "cluster buying" in summarise_insiders([
        {"owner_name": n, "acquisition_or_disposition": "A",
         "securities_transacted": 100, "transaction_price": 10.0} for n in "ABC"])
    assert "10b5-1" in summarise_insiders([
        {"owner_name": "A", "acquisition_or_disposition": "D",
         "securities_transacted": 1, "transaction_price": 1.0,
         "footnote": "pursuant to a Rule 10b5-1 plan"}])
    assert summarise_news([{"title": "T", "date": "2026-01-01", "source": "s"}]) == (
        "[2026-01-01] T (s)",)
    print("openbb_provider self-check passed")


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        _demo()
        raise SystemExit(0)
    feed = CatalystFeed()
    ev = asyncio.run(feed.catalysts(sys.argv[1] if len(sys.argv) > 1 else "AAPL"))
    for line in ev.as_notes():
        print("-", line)
