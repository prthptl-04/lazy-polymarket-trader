"""Catalysts — the dated events, filings and flow that move a price on the day.

Named for what it produces, not where it fetches from. It was
`openbb_provider.py` until the earnings calendar, the filing index and the
options-implied move arrived off the fund's own Robinhood session; a module
named for one of its three sources is a name that misleads within a month.

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
    # Async callable -> market-wide earnings rows. Supplied by the Robinhood
    # adapter, whose MCP surface carries the calendar for free; one call per
    # cycle serves every candidate, so it is fetched outside this class.
    earnings_source: Optional[Callable[[], Any]] = None
    # symbol -> recent 8-K rows.
    filing_source: Optional[Callable[[str, str], Any]] = None
    # symbol -> level-2 book.
    depth_source: Optional[Callable[[str], Any]] = None
    # (symbol, spot, after_date) -> implied move %, or None.
    implied_move_source: Optional[Callable[[str, float, str], Any]] = None
    # Injected so tests never touch the network and never import OpenBB.
    _import: Optional[Callable[[], Any]] = None

    def _obb(self) -> Any:
        if self._import is not None:
            return self._import()
        from openbb import obb          # lazy: the fund runs without it
        return obb

    async def catalysts(self, symbol: str, asset_class: str = "equity", *,
                        spot: Optional[float] = None,
                        stop_distance_pct: Optional[float] = None,
                        equities_open: bool = True) -> CatalystEvidence:
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

        if asset_class == "equity":
            earnings_date = await self._equities_only(symbol, notes, degraded)
            if self.filing_source is not None:
                since = (datetime.now(timezone.utc).date()
                         - timedelta(days=FILING_RECENT_DAYS)).isoformat()
                rows = await self._safe(obb, "filings", symbol, degraded,
                                        lambda: self.filing_source(symbol, since),
                                        already_async=True)
                if rows is not None:
                    notes.append(summarise_filings(rows))
            if self.depth_source is not None:
                book = await self._safe(obb, "order book", symbol, degraded,
                                        lambda: self.depth_source(symbol),
                                        already_async=True, raw=True)
                if book is not None:
                    notes.append(summarise_depth(book or {}, equities_open))
            # Gated: three round trips and a hundred-row strike list. Worth it
            # only when there is an event for the options to be pricing.
            if (self.implied_move_source is not None and earnings_date
                    and spot and _days_until(earnings_date) is not None
                    and 0 <= _days_until(earnings_date) <= IMPLIED_MOVE_WINDOW_DAYS):
                try:
                    pct = await asyncio.wait_for(
                        self.implied_move_source(symbol, spot, earnings_date),
                        timeout=self.timeout_seconds)
                    line = summarise_implied_move(pct, stop_distance_pct,
                                                  earnings_date, symbol)
                    if line:
                        notes.append(line)
                except Exception as e:
                    degraded.append(f"implied move ({type(e).__name__})")

        if not notes:
            return CatalystEvidence(
                reason="every catalyst source failed this cycle",
                degraded=tuple(degraded))
        return CatalystEvidence(notes=tuple(notes), available=True,
                                degraded=tuple(degraded))

    async def _equities_only(self, symbol: str, notes: list[str],
                             degraded: list[str]) -> Optional[str]:
        """Earnings line, and the date it found, for the implied-move gate."""
        if self.earnings_source is None:
            return None
        try:
            rows = list(await asyncio.wait_for(self.earnings_source(),
                                               timeout=self.timeout_seconds) or [])
        except Exception as e:
            degraded.append(f"earnings calendar ({type(e).__name__})")
            return None
        notes.append(summarise_earnings(rows, symbol))
        for row in rows:
            if str(row.get("symbol", "")).upper() != symbol.upper():
                continue
            report = row.get("report") or {}
            if (row.get("eps") or {}).get("actual") is None and report.get("date"):
                return str(report["date"])
        return None

    async def _safe(self, obb: Any, label: str, symbol: str,
                    degraded: list[str], call: Callable[[], Any],
                    *, already_async: bool = False,
                    raw: bool = False) -> Optional[Any]:
        """One endpoint. A failure degrades that source and nothing else.

        Most OpenBB calendars require a paid provider key, so a gated endpoint
        is the NORMAL case on a keyless install — losing the whole block over
        one of them would leave the feed showing nothing at all.
        """
        try:
            awaitable = call() if already_async else asyncio.to_thread(call)
            result = await asyncio.wait_for(awaitable, timeout=self.timeout_seconds)
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
        return result if raw else _rows(result)


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


def summarise_earnings(rows: list[dict], symbol: str,
                       today: Optional[str] = None) -> str:
    """When does THIS name report, and has it already?

    The single most decision-relevant catalyst a swing fund has: a technically
    perfect setup entered thirty-six hours before a print is a coin flip, and
    this is the only line on the page that says so.

    Three distinctions the raw feed makes and a naive reading loses:
      - `eps.actual` populated means it has ALREADY reported. Reading a past
        print as an upcoming one inverts the advice entirely.
      - `verified: false` is Robinhood's own flag for an unconfirmed date.
        Presenting a guess as a fact is how a seat vetoes a good trade for
        nothing.
      - `timing` am/pm decides whether the risk is tonight or tomorrow morning.
    """
    ref = _date(today) or datetime.now(timezone.utc).date()
    mine = [r for r in rows if str(r.get("symbol", "")).upper() == symbol.upper()]
    if not mine:
        return (f"Earnings: no earnings scheduled for {symbol} in the window checked. "
                "This is a positive absence of a known event, not an unchecked one.")

    out = []
    for row in mine:
        report = row.get("report") or {}
        when = _date(report.get("date"))
        if when is None:
            continue
        eps = row.get("eps") or {}
        actual, estimate = _num(eps.get("actual")), _num(eps.get("estimate"))
        if actual is not None:
            verdict = ("in line with" if estimate is None or abs(actual - estimate) < 1e-9
                       else "a beat against" if actual > estimate
                       else "a miss against")
            out.append(f"Earnings: {symbol} already REPORTED on {when} — "
                       f"{actual:.2f} vs {estimate if estimate is None else f'{estimate:.2f}'} "
                       f"estimate, {verdict} expectations. The event risk is behind it.")
            continue
        days = (when - ref).days
        timing = {"am": "before the open", "pm": "after the close"}.get(
            str(report.get("timing") or "").lower(), "time of day unconfirmed")
        tentative = "" if report.get("verified") else " (date TENTATIVE, not confirmed)"
        est = f" Consensus EPS {estimate:.2f}." if estimate is not None else ""
        horizon = ("TODAY" if days == 0 else
                   f"in {days} days" if days > 0 else f"{abs(days)} days ago")
        out.append(f"Earnings: {symbol} reports {horizon} on {when}, {timing}{tentative}."
                   f"{est} A position opened now carries that event.")
    return " ".join(out) or (
        f"Earnings: a row exists for {symbol} but carries no usable date.")


# A wall is a resting level this many times the median size on its own side.
WALL_MULTIPLE = 5.0
# Filings older than this stop being an event and start being background.
FILING_RECENT_DAYS = 21
# Only price an event the options are actually pricing.
IMPLIED_MOVE_WINDOW_DAYS = 10


def _days_until(date_str: str) -> Optional[int]:
    when = _date(date_str)
    return None if when is None else (when - datetime.now(timezone.utc).date()).days


def summarise_implied_move(implied_move_pct: Optional[float],
                           stop_distance_pct: Optional[float],
                           expiry: str, symbol: str) -> str:
    """What the options price for the event, against the stop we intend to use.

    This is the one line that connects the catalyst block to the exit plan, and
    it is the only reason the options call is worth making. "Earnings Thursday"
    is trivia. "The options price a 6.8% move and your stop is 4.1% away" is a
    decision — either size down, wait for the print, or accept that a normal
    reaction stops you out.

    No stop means no comparison. A candidate without an exit plan is
    pre-screened out before this runs, and inventing a distance to complete the
    sentence would put a fabricated number in an evidence block.
    """
    if implied_move_pct is None:
        return ""
    head = (f"Implied move: the {expiry} options price a ±{implied_move_pct:.1f}% "
            f"move in {symbol} around the event.")
    if stop_distance_pct is None or stop_distance_pct <= 0:
        return head + " No exit plan yet to compare it against."
    if implied_move_pct > stop_distance_pct:
        ratio = implied_move_pct / stop_distance_pct
        return (f"{head} The proposed stop is {stop_distance_pct:.1f}% away — the "
                f"implied move is {ratio:.1f}x wider than the stop, so an ordinary "
                f"reaction takes the position out. Size down, or wait for the print.")
    return (f"{head} The proposed stop is {stop_distance_pct:.1f}% away, so the "
            f"stop should survive a move of the size the options expect.")


def summarise_filings(rows: list[dict], today: Optional[str] = None) -> str:
    """Recent material filings. An 8-K is a company saying something happened.

    Old filings are excluded rather than listed: a 10-K from last January is
    background the Analyst seat already reads through the fundamentals, and
    presenting it beside a two-day-old 8-K teaches the seats that everything
    here is equally current.
    """
    ref = _date(today) or datetime.now(timezone.utc).date()
    recent = []
    for row in rows:
        when = _date(row.get("date_filed"))
        if when is None:
            continue
        age = (ref - when).days
        if 0 <= age <= FILING_RECENT_DAYS:
            form = str(row.get("form_type") or "filing")
            desc = str(row.get("description") or "").strip()
            recent.append(f"{form} filed {age} days ago"
                          + (f" ({desc})" if desc else ""))
    if not recent:
        return (f"Filings: no material filings in the last {FILING_RECENT_DAYS} days. "
                "Checked, not merely absent.")
    return ("Filings: " + "; ".join(recent[:4])
            + ". An 8-K is the company itself saying something happened — read it "
              "as a dated event, not as background.")


def summarise_depth(book: dict, equities_open: bool) -> str:
    """Resting size either side, and any wall worth placing a stop around.

    NOT about market impact. At this account size a $150 order does not move a
    liquid book, and saying otherwise would be theatre. The real use is stop
    placement: a stop sitting just below a large resting bid is in a different
    place from one sitting in thin air, and only the ladder shows that.

    An empty book while the market is CLOSED is not an absence of liquidity.
    Robinhood returns empty outside hours, and rendering that as "untradeable"
    would prime the Risk seat against every name every weekend — which is
    exactly when the fund trades crypto.
    """
    bids = [lvl for lvl in (book.get("bids") or []) if _num(lvl.get("quantity"))]
    asks = [lvl for lvl in (book.get("asks") or []) if _num(lvl.get("quantity"))]
    if not bids and not asks:
        if not equities_open:
            return ("Order book: the equity market is closed, so no book is "
                    "published. This says nothing about liquidity in hours.")
        return ("Order book: NO RESTING LIQUIDITY on either side during open "
                "hours. Treat any fill assumption with suspicion.")

    parts = [f"Order book: {len(bids)} bid levels, {len(asks)} ask levels."]
    wall = _wall(bids)
    if wall:
        price, qty = wall
        parts.append(f"A bid wall of {qty:,.0f} shares rests at {price:,.2f} — "
                     "a stop placed just beneath it sits behind real size rather "
                     "than in thin air.")
    else:
        parts.append("No outsized resting level on the bid side, so there is no "
                     "wall to anchor a stop to.")
    return " ".join(parts)


def _wall(levels: list[dict]) -> Optional[tuple[float, float]]:
    """The largest level, if it dwarfs the median on its own side."""
    sizes = [_num(l.get("quantity")) or 0.0 for l in levels]
    if len(sizes) < 3:
        return None
    median = sorted(sizes)[len(sizes) // 2]
    if median <= 0:
        return None
    biggest = max(levels, key=lambda l: _num(l.get("quantity")) or 0.0)
    qty = _num(biggest.get("quantity")) or 0.0
    price = _num(biggest.get("price"))
    if price is None or qty < median * WALL_MULTIPLE:
        return None
    return price, qty


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
    print("catalysts self-check passed")


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        _demo()
        raise SystemExit(0)
    feed = CatalystFeed()
    ev = asyncio.run(feed.catalysts(sys.argv[1] if len(sys.argv) > 1 else "AAPL"))
    for line in ev.as_notes():
        print("-", line)
