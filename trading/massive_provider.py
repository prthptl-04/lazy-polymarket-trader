"""Massive market data — the daemon's price source.

`MarketDataProvider` over Massive's REST API. This is the path the fund
process uses; the `massive` MCP server is a separate, session-bound channel
useful only for interactive exploration.

Verified live 2026-09-12 on the $29 stocks plan:
- `/v2/aggs/...` returns equity **and crypto** bars (crypto is entitled).
- `/stocks/financials/v1/*` returns NOT_ENTITLED, so `get_financials` returns
  None and the Altman/Piotroski screens report NOT AVAILABLE rather than
  guessing. That is the honest degradation the seats are built to handle.

stdlib urllib, no new dependency — this makes a handful of calls per cycle,
not a stream.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from finance.exits import Bar
from finance.quality import Financials
from trading.market_data import PriceHistory
from trading.venues.base import Quote

BASE_URL = "https://api.massive.com"
DEFAULT_TIMEOUT = 20.0


@dataclass
class MassiveProvider:
    """Bars from Massive. `fetch` is injectable so tests never hit the network."""

    api_key: Optional[str] = None
    base_url: str = BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    fetch: Any = None
    # Massive's plan has no financials; SEC EDGAR serves them free.
    financials: Any = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("MASSIVE_API_KEY")
        self.fetch = self.fetch or self._http_get

    # ---------- MarketDataProvider ----------

    async def get_history(self, symbol: str, *, lookback: int = 60) -> Optional[PriceHistory]:
        # Calendar days, padded for weekends/holidays so `lookback` trading
        # bars actually come back rather than ~5/7ths of them.
        end = date.today()
        start = end - timedelta(days=int(lookback * 1.6) + 10)
        path = (f"/v2/aggs/ticker/{_ticker(symbol)}/range/1/day/"
                f"{start.isoformat()}/{end.isoformat()}")
        data = self._call(path, {"adjusted": "true", "sort": "asc", "limit": 50000})
        rows = (data or {}).get("results") or []
        if not rows:
            return None
        rows = rows[-lookback:] if lookback else rows
        return PriceHistory(
            bars=tuple(Bar(high=r["h"], low=r["l"], close=r["c"]) for r in rows),
            closes=tuple(r["c"] for r in rows),
            volumes=tuple(r.get("v", 0.0) for r in rows),
        )

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """Previous close. The $29 plan is 15-minute delayed, so this is a
        reference price, not a tradable one — the venue's own quote is what an
        order should price against."""
        data = self._call(f"/v2/aggs/ticker/{_ticker(symbol)}/prev", {"adjusted": "true"})
        rows = (data or {}).get("results") or []
        if not rows:
            return None
        close = rows[0].get("c")
        return Quote(symbol=symbol, bid=None, ask=None, last=close)

    async def get_news(self, symbol: str, *, limit: int = 6) -> list[dict]:
        """Recent articles with the publisher's per-ticker sentiment.

        Verified entitled on the $29 plan (2026-09-12). Each article carries an
        `insights` list with a sentiment and a one-line reasoning PER TICKER,
        which is what makes this usable: an article about the whole sector is
        not evidence about our symbol, and the per-ticker split says which is
        which.

        The sentiment is the publisher's, not ours. It reaches the seats
        labelled that way — a vendor's label is a data point, not a verdict.
        """
        data = self._call("/v2/reference/news", {
            "ticker": _ticker(symbol).replace("X:", ""),
            "limit": max(1, min(50, limit)),
            "order": "desc",
            "sort": "published_utc",
        })
        rows = (data or {}).get("results") or []
        out: list[dict] = []
        for a in rows[:limit]:
            insight = next(
                (i for i in (a.get("insights") or [])
                 if str(i.get("ticker", "")).upper() == symbol.upper()),
                None,
            )
            out.append({
                "title": a.get("title"),
                "publisher": ((a.get("publisher") or {}).get("name")
                              if isinstance(a.get("publisher"), dict) else a.get("publisher")),
                "published": a.get("published_utc"),
                "sentiment": (insight or {}).get("sentiment"),
                "why": (insight or {}).get("sentiment_reasoning"),
                "url": a.get("article_url"),
            })
        return out

    async def get_financials(
        self, symbol: str
    ) -> Optional[tuple[Financials, Optional[Financials]]]:
        """Massive returns NOT_ENTITLED on the $29 plan (verified 2026-09-12),
        so this delegates to SEC EDGAR when one is attached.

        With no delegate it returns None, and the candidate builder reports the
        quality screens as NOT AVAILABLE — better than a fabricated score.
        """
        if self.financials is None:
            return None
        return await self.financials.get_financials(symbol)

    # ---------- internals ----------

    def _call(self, path: str, params: dict) -> Optional[dict]:
        if not self.api_key:
            return None
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        try:
            body = self.fetch(url, self.api_key, self.timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                TimeoutError, ValueError):
            # A dead or slow feed degrades to "no data", which the pre-screen
            # already treats as a skip. It must never take down a cycle.
            #
            # Deliberately NOT a bare `except Exception`: that would swallow a
            # TypeError from a mis-wired fetch and present a programming bug as
            # an empty feed, which is exactly how this went unnoticed once.
            return None
        if not isinstance(body, dict):
            return None
        if str(body.get("status", "OK")).upper() in ("ERROR", "NOT_AUTHORIZED"):
            return None
        return body

    @staticmethod
    def _http_get(url: str, api_key: str, timeout: float) -> dict:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 — https, fixed host
            return json.load(r)


_CRYPTO_BASES = ("BTC", "ETH", "SOL", "DOGE", "XRP", "LTC", "ADA", "AVAX", "LINK", "DOT")


def _ticker(symbol: str) -> str:
    """Crypto needs the X: prefix and a USD quote; equities pass through.

    Accepts the hyphenated pair too. `BTC-USD` used to fall through to the
    final `return s` and be sent as an equity ticker, so `get_history` came
    back None and every crypto candidate was pre-screened out with "no price
    data available" — while the venue, which requires exactly that spelling,
    was the only one getting it right.
    """
    s = symbol.upper()
    if s.startswith("X:"):
        return s
    base = s.split("-")[0]
    if base in _CRYPTO_BASES:
        return f"X:{base}USD"
    return s
