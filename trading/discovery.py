"""Market scout — the fund finds its own candidates.

The watchlist is gone. A hand-typed list of tickers is a standing bet that the
opportunity is where you last looked, and it goes stale silently. One Massive
call returns the previous session for every US ticker (~12,500 rows verified
2026-09-12), so the fund screens the whole market each cycle instead.

The screen is **deterministic and runs before any LLM sees anything**. Twelve
thousand tickers through a round table would cost more than the account is
worth; arithmetic narrows it to a handful, and only those get debated.

Filters, and why each exists rather than being a taste preference:

- **Minimum price.** Sub-$5 names have spreads that eat any edge and are the
  usual home of manipulation. The fund cannot trade around that at its size.
- **Minimum dollar volume.** This is an exit-liquidity test, not a popularity
  one. A position you cannot leave is a position you did not really size.
- **Maximum move.** A name already up 40% today has had its move; entering
  after it is chasing, and the ATR stop would sit absurdly far away.
- **Minimum move.** Something has to be happening. A flat tape is not a setup.

Ranking favours unusual *volume* over the biggest price move, because volume
confirms participation while a large move on thin volume is often one motivated
buyer and a wide spread.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

BASE_URL = "https://api.massive.com"
GROUPED = "/v2/aggs/grouped/locale/us/market/stocks/{day}"


@dataclass(frozen=True)
class ScoutCandidate:
    symbol: str
    close: float
    move_pct: float
    dollar_volume: float
    trades: int
    score: float
    reason: str

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol, "close": round(self.close, 4),
            "move_pct": round(self.move_pct, 3),
            "dollar_volume": round(self.dollar_volume, 0),
            "score": round(self.score, 4), "reason": self.reason,
        }


@dataclass
class MarketScout:
    """Screens the whole US tape down to a shortlist."""

    api_key: Optional[str] = None
    base_url: str = BASE_URL
    fetch: Any = None

    min_price: float = 5.0
    max_price: float = 2_000.0
    min_dollar_volume: float = 10_000_000.0
    min_abs_move_pct: float = 2.0
    max_abs_move_pct: float = 25.0
    limit: int = 10
    exclude: tuple[str, ...] = ()

    _cache: dict[str, list[ScoutCandidate]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("MASSIVE_API_KEY")
        self.fetch = self.fetch or self._http_get

    # ---------- public ----------

    def scan(self, *, day: Optional[date] = None, limit: Optional[int] = None) -> list[ScoutCandidate]:
        """Shortlist for one session. Cached per day — the grouped endpoint
        returns a completed session, so re-fetching within a cycle is waste."""
        target = self._resolve_day(day)
        key = target.isoformat()
        if key not in self._cache:
            self._cache[key] = self._screen(self._grouped(target))
        return self._cache[key][: (limit or self.limit)]

    # ---------- internals ----------

    def _grouped(self, day: date) -> list[dict]:
        if not self.api_key:
            return []
        url = f"{self.base_url}{GROUPED.format(day=day.isoformat())}?adjusted=true"
        try:
            body = self.fetch(url, self.api_key)
        except Exception:
            return []
        if not isinstance(body, dict):
            return []
        return body.get("results") or []

    def _screen(self, rows: list[dict]) -> list[ScoutCandidate]:
        excluded = {s.upper() for s in self.exclude}
        out: list[ScoutCandidate] = []
        for r in rows:
            symbol = str(r.get("T") or "").upper()
            close, open_, volume = r.get("c"), r.get("o"), r.get("v")
            if not symbol or symbol in excluded:
                continue
            if close is None or open_ in (None, 0) or volume is None:
                continue
            # Units, warrants and preferreds: thin, and not what we screen for.
            if not symbol.isalpha() or len(symbol) > 5:
                continue
            if not (self.min_price <= close <= self.max_price):
                continue
            dollar_volume = close * volume
            if dollar_volume < self.min_dollar_volume:
                continue
            move = (close - open_) / open_ * 100.0
            if not (self.min_abs_move_pct <= abs(move) <= self.max_abs_move_pct):
                continue

            trades = int(r.get("n") or 0)
            # Volume carries more weight than the move: a big move on thin
            # volume is usually one motivated buyer and a wide spread.
            score = (dollar_volume ** 0.5) * (1.0 + abs(move) / 100.0)
            out.append(ScoutCandidate(
                symbol=symbol, close=float(close), move_pct=move,
                dollar_volume=dollar_volume, trades=trades, score=score,
                reason=(f"{move:+.1f}% on ${dollar_volume/1e6:,.0f}M traded"),
            ))
        out.sort(key=lambda c: c.score, reverse=True)
        return out

    @staticmethod
    def _resolve_day(day: Optional[date]) -> date:
        """Most recent weekday. The grouped endpoint has no weekend session, and
        asking for one returns an empty set that looks like 'no opportunities'."""
        target = day or date.today() - timedelta(days=1)
        while target.weekday() >= 5:
            target -= timedelta(days=1)
        return target

    @staticmethod
    def _http_get(url: str, api_key: str) -> dict:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=30) as r:   # noqa: S310 — https, fixed host
            return json.load(r)
