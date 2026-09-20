"""Kalshi's public read surface — no key, no signature, no account.

Everything needed to model, backtest and paper-trade KXBTC15M is served
unauthenticated:

    /markets                                 strike, open/close, result, book top
    /live_data/events/{event}                BRTI at 1 Hz, plus the maturity stamp
    /series/{s}/markets/{t}/candlesticks     yes_bid / yes_ask OHLC per minute

That matters more than it sounds. The strategy's viability question — "is the
net edge positive after fees across a few hundred windows" — is answerable
before a single credential exists, which is the order those two things should
happen in. Signing lives in `auth.py` and is needed only to place an order.

Two shapes of this API bite, and both are handled here rather than at the call
sites:

**Money arrives as decimal strings.** `"0.6400"`, not `64`. Parsing them at the
boundary keeps float cents out of the model, where 0.64 and 64 differ by a
factor of a hundred and both look plausible.

**Ticks are not uniform.** `price_level_structure: "tapered_deci_cent"` means
$0.001 below 10c and above 90c, $0.01 between. The tails are priced ten times
more finely than the middle — which is also where the fee is smallest, so it is
the part of the curve the strategy actually lives on.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
SERIES_TICKER = "KXBTC15M"

# Kalshi publishes no hard public rate limit; this is politeness, not a spec.
MIN_INTERVAL_SECONDS = 0.15

# The tapered tick ladder, as the exchange reports it.
DECI_CENT_BELOW = 0.10
DECI_CENT_ABOVE = 0.90


def tick_size(price: float) -> float:
    """Smallest legal price increment at `price`.

    A limit rounded to the wrong grid is rejected by the exchange, and a maker
    strategy that cannot rest an order is not a maker strategy.
    """
    return 0.001 if price < DECI_CENT_BELOW or price > DECI_CENT_ABOVE else 0.01


def round_to_tick(price: float, *, up: bool) -> float:
    """Snap to the legal grid, in the direction that does not improve our price.

    Rounding a bid up or an offer down would quietly cross further than
    intended; every rounding here is away from us.
    """
    step = tick_size(price)
    n = price / step
    # Nudge before rounding: 0.07/0.001 is 69.99999999999999 in binary float,
    # and floor() on that silently loses a tick.
    n = round(n, 6)
    snapped = (int(n) + 1 if n > int(n) else int(n)) if up else int(n)
    return round(snapped * step, 4)


@dataclass(frozen=True)
class Window:
    """One 15-minute contract, with everything needed to price or grade it."""

    ticker: str
    event_ticker: str
    open_ts: float
    close_ts: float
    floor_strike: Optional[float]
    result: str = ""                       # "yes" | "no" | "" while live
    index: tuple[tuple[float, float], ...] = ()      # (unix_seconds, BRTI)
    book: tuple[dict, ...] = ()                      # minute candles

    @property
    def settled(self) -> bool:
        return self.result in ("yes", "no")

    def tau_at(self, ts: float) -> float:
        """Seconds until the contract stops being random."""
        return max(0.0, self.close_ts - ts)

    def index_at(self, ts: float) -> Optional[float]:
        """Last BRTI print at or before `ts`.

        Deliberately backward-looking: using the next print would leak a value
        the strategy could not have seen, which is the single easiest way to
        backtest a profit that does not exist.
        """
        best = None
        for t, v in self.index:
            if t <= ts:
                best = v
            else:
                break
        return best

    def index_mean(self, start_ts: float, end_ts: float) -> Optional[float]:
        vals = [v for t, v in self.index if start_ts <= t < end_ts]
        return sum(vals) / len(vals) if vals else None


@dataclass
class KalshiPublic:
    """Read-only client. Holds no credential and cannot place an order."""

    base_url: str = DEFAULT_BASE_URL
    timeout: float = 20.0
    # Injectable so tests never touch the network.
    fetch: Optional[Callable[[str], dict]] = None
    _last_call: float = field(default=0.0, init=False, repr=False)

    # ---------- endpoints ----------

    def markets(
        self,
        *,
        series_ticker: str = SERIES_TICKER,
        status: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        params: dict[str, object] = {"series_ticker": series_ticker, "limit": limit}
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        data = self._get("/markets", params) or {}
        return data.get("markets") or [], data.get("cursor") or None

    def settled_markets(self, *, limit: int = 200) -> list[dict]:
        """Walk the cursor until `limit` settled windows are collected."""
        out: list[dict] = []
        cursor = None
        while len(out) < limit:
            page, cursor = self.markets(status="settled", limit=100, cursor=cursor)
            if not page:
                break
            out.extend(page)
            if not cursor:
                break
        return out[:limit]

    def index_series(self, event_ticker: str) -> tuple[list[tuple[float, float]], Optional[float]]:
        """The 1 Hz BRTI series for an event, and its maturity timestamp.

        This is the series the contract settles on. Not Coinbase, not a spot
        feed — CF Benchmarks' BRTI, which is what `rules_secondary` names and
        what makes our settlement reconcilable against Kalshi's own result.
        """
        data = self._get(f"/live_data/events/{urllib.parse.quote(event_ticker)}") or {}
        details = ((data.get("live_data") or {}).get("details")) or {}
        series = [
            (float(p["t"]) / 1000.0, float(p["v"]))
            for p in (details.get("timeseries") or [])
            if p.get("t") is not None and p.get("v") is not None
        ]
        series.sort(key=lambda p: p[0])
        maturity = details.get("maturity_ts_ms")
        return series, (float(maturity) / 1000.0 if maturity else None)

    def candlesticks(
        self,
        ticker: str,
        *,
        start_ts: float,
        end_ts: float,
        series_ticker: str = SERIES_TICKER,
        period_interval: int = 1,
    ) -> list[dict]:
        """Per-minute OHLC of the book. One minute is the finest Kalshi offers."""
        data = self._get(
            f"/series/{urllib.parse.quote(series_ticker)}"
            f"/markets/{urllib.parse.quote(ticker)}/candlesticks",
            {"start_ts": int(start_ts), "end_ts": int(end_ts),
             "period_interval": period_interval},
        ) or {}
        return data.get("candlesticks") or []

    # ---------- composition ----------

    def window(self, market: dict, *, with_book: bool = True) -> Optional[Window]:
        """Assemble a market row plus its index and book history."""
        open_ts = parse_ts(market.get("open_time"))
        close_ts = parse_ts(market.get("close_time"))
        ticker, event = market.get("ticker"), market.get("event_ticker")
        if not (ticker and event and open_ts and close_ts):
            return None

        index, maturity = self.index_series(event)
        # Trust the market's own close over the event's maturity stamp — the
        # event covers a rolling hour, the market is the thing that settles.
        book = (
            self.candlesticks(ticker, start_ts=open_ts - 60, end_ts=close_ts + 60)
            if with_book else []
        )
        return Window(
            ticker=ticker,
            event_ticker=event,
            open_ts=open_ts,
            close_ts=close_ts,
            floor_strike=_float(market.get("floor_strike")),
            result=str(market.get("result") or ""),
            index=tuple(index),
            book=tuple(book),
        )

    # ---------- internals ----------

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        self._throttle()
        try:
            return (self.fetch or self._http_get)(url)
        except Exception:
            # A read failure is missing data, not a crash. Callers treat a None
            # window as "skip", which is the right answer for a gap in a feed
            # we are about to base money on.
            return None

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < MIN_INTERVAL_SECONDS:
            time.sleep(MIN_INTERVAL_SECONDS - gap)
        self._last_call = time.monotonic()

    def _http_get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — https, api.elections.kalshi.com
            return json.load(r)


# ---------- parsing ----------

def _float(value: object) -> Optional[float]:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def dollars(value: object) -> Optional[float]:
    """Parse Kalshi's decimal-string money into a float in dollars.

    `"0.6400"` -> 0.64. An empty string means "no price", which is not zero: a
    market with no bid is not a market bid at zero, and treating it as one
    invents a free contract.
    """
    if value is None or value == "":
        return None
    price = _float(value)
    if price is None or not (0.0 <= price <= 1.0):
        return None
    return price


def parse_ts(value: object) -> Optional[float]:
    """RFC3339 -> unix seconds."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            tzinfo=timezone.utc if value.endswith("Z") else None
        ).timestamp()
    except ValueError:
        return None


def candle_quote(candle: dict) -> tuple[Optional[float], Optional[float]]:
    """(yes_bid, yes_ask) at the close of a minute candle.

    The close, not the mean: the mean is an average over a minute in which we
    could not have traded at the average.
    """
    price = candle.get("yes_bid") or {}
    ask = candle.get("yes_ask") or {}
    return dollars(price.get("close_dollars")), dollars(ask.get("close_dollars"))
