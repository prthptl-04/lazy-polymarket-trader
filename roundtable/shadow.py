"""Score every deliberation, whether or not it became a trade.

The premise this corrects: outcomes only existed when a position closed, so a
committee that stood aside 33 times out of 36 produced nothing to learn from.
Seat weights, the confidence shrink and the post-mortem lessons are all gated
behind 30 resolved outcomes, so declining meant never calibrating, which meant
declining again. The system could not bootstrap.

But a deliberation is a PREDICTION — a named instrument, a direction, a
confidence, at a known price — and the market resolves it whether or not we took
the position. NEAR-USD moved regardless of whether we bought it. Every debate is
a scoreable call, and the fund was throwing all of them away.

So seats are scored on every call they make. Calibration starts from debate one,
and a seat that is consistently wrong loses weight without the fund having to
lose money to find out.

TWO BOUNDARIES, both of which matter more than the feature.

**A shadow outcome is not a trade.** Rule #13 requires 50 paper TRADES before a
live flip, and the live gate counts closed round trips rather than predictions.
Nothing here can open that gate.

**A shadow outcome must not fit the confidence shrink.** The shrink maps stated
confidence to a win probability for POSITIONS, and a position is bounded by its
stop: it can be taken out by a move that later reverses, while a 24-hour price
change never is. Predictions therefore look better than the trades they would
have become, and a shrink fitted on them would be optimistic — which sizes
bigger, the expensive direction to be wrong in. Marked with `SHADOW_NOTE` and
excluded by `fit_confidence_shrink`.
"""

from __future__ import annotations

import logging
import time
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

logger = logging.getLogger(__name__)

# Marks an outcome as scored from the market rather than from a position.
SHADOW_NOTE = "shadow: scored from the tape, no position was taken"

# How long a call is given to be right. The fund targets 3xATR against a 2xATR
# stop, which on daily bars is a move of a day or two — so a call scored after
# an hour is measuring noise, and one scored after a week is measuring a
# different thesis.
DEFAULT_HORIZON_HOURS = 24.0
# Don't rescore history forever; anything older than this was a different market.
MAX_AGE_HOURS = 24.0 * 7


def shadow_return(signal: Optional[str], entry: Optional[float],
                  later: Optional[float]) -> Optional[float]:
    """The move, signed so that "right" is always positive.

    A correct bearish call shows a NEGATIVE price change; scoring on the raw
    sign would mark every right short as a loss, which is the same trap the
    replay harness had to avoid.

    `None` for a neutral call — it made no directional claim, so there is
    nothing to be right about, and scoring it as wrong whenever the price moved
    would punish correct caution. `None` also for an unusable price: zero is a
    real result (a flat market) and unknown is not.
    """
    if not entry or entry <= 0 or later is None or later <= 0:
        return None
    move = (later - entry) / entry
    if signal == "bullish":
        return move
    if signal == "bearish":
        return -move
    return None


_LAST_PRICE = re.compile(r"^Last price:\s*([0-9]*\.?[0-9]+)", re.M)


def _price_from_evidence(payload: dict) -> Optional[float]:
    """Recover the price from a stored evidence block.

    A fallback for HISTORY only. Deliberations written before `price` became a
    payload field still carry the rendered block, and the alternative to
    parsing it is discarding real calls the fund has already paid for — 34 of
    them at the time this was written, which is above the sample bar that
    switches calibration on.

    New rows carry the field, so this reads them never. Parsing rendered text
    is fragile and it is deliberately not the primary path.
    """
    match = _LAST_PRICE.search(str(payload.get("evidence") or ""))
    if match is None:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def quote_symbol(row: dict) -> Optional[str]:
    """The symbol a QUOTE can actually be fetched for.

    Ten deliberations sat due and unscored for 30 hours because they were
    recorded as `BTC` and `ETH` — the bare form the config override uses
    (`FUND_CRYPTO_WATCHLIST=BTC,ETH`). The venue spells every crypto pair
    `BASE-USD` and cannot quote a bare base, so the price came back empty every
    cycle, the row was skipped exactly as a provider blip is, and it would have
    aged past `MAX_AGE_HOURS` and been dropped without anyone noticing. Ten
    samples out of a calibration set that needs thirty to switch on.

    The mapping is the documented convention read backwards, not a guess:
    `trading.fund.classify_asset_class` calls a symbol crypto BECAUSE it ends
    `-USD`, and says in as many words that the watchlist override uses bare
    symbols with no suffix. The stored `asset_class` says which of the two this
    row is, so nothing has to be inferred from the string alone.

    Equity tickers are returned untouched: no US ticker contains a hyphen, and
    appending a quote currency to one would invent an instrument.
    """
    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    if row.get("asset_class") == "crypto" and "-" not in symbol:
        return f"{symbol}-USD"
    return symbol


def is_shadow(outcome: dict) -> bool:
    return str((outcome or {}).get("notes") or "").startswith("shadow:")


@dataclass
class ShadowResolver:
    """Turns due deliberations into scored outcomes."""

    memory: Any
    # symbol -> current price. A LAST RESORT, and None in the fund.
    #
    # It used to be how prices arrived, and it wedged the whole process: the
    # callable the fund supplied reached an async venue, so it ran the
    # coroutine on a worker thread's own loop and blocked the cycle's loop on
    # the result. The venue's MCP session is pinned to the cycle's loop, so
    # that coroutine never finished and the block was permanent — no cycles, no
    # HTTP, nothing, until the process was killed.
    #
    # Prices now arrive as `prices` from the caller, fetched on the caller's
    # own loop (`FundLoop._score_past_calls`), the same way the crypto pair
    # list arrives via `refresh_pairs`. This hook stays for tests and for a
    # genuinely sync provider.
    quote: Optional[Callable[[str], Optional[float]]] = None
    horizon_hours: float = DEFAULT_HORIZON_HOURS
    max_age_hours: float = MAX_AGE_HOURS
    limit: int = 200

    def due_symbols(self) -> tuple[str, ...]:
        """The symbols `resolve_due` would need a price for, right now.

        Split out so an async caller can fetch those prices on its OWN loop and
        hand them in. The filter is `_due_rows`, shared with `resolve_due`, so
        the two cannot drift into asking for one set and scoring another.
        """
        try:
            rows, _ = self._due_rows()
        except Exception:
            logger.exception("could not read deliberations to score")
            return ()
        return tuple(dict.fromkeys(
            q for q in (quote_symbol(r) for r in rows) if q))

    def _due_rows(self) -> tuple[list, set]:
        """Completed deliberations past the horizon and not yet scored."""
        rows = self.memory.recent_deliberations(limit=self.limit)
        already = {o["thesis_id"] for o in
                   self.memory.resolved_outcomes(limit=self.limit * 2)}
        now = time.time()
        due = []
        for row in rows:
            tid = row.get("thesis_id")
            if not tid or tid in already or row.get("status") != "complete":
                continue
            age_h = (now - float(row.get("created") or now)) / 3600.0
            if self.horizon_hours <= age_h <= self.max_age_hours:
                due.append(row)
        return due, already

    def resolve_due(self, prices: Optional[Mapping[str, float]] = None) -> int:
        """Score every completed deliberation past its horizon. Returns the
        count. Never raises — this runs inside a cycle, and a failure to learn
        must not become a failure to trade.

        `prices` is symbol -> last price, fetched by the caller on its own
        loop. A symbol missing from it is skipped, exactly as a failed quote
        is: a sample scored against a price we never saw is worse than no
        sample."""
        try:
            rows, _ = self._due_rows()
        except Exception:
            logger.exception("could not read deliberations to score")
            return 0

        scored = 0
        for row in rows:
            tid = row.get("thesis_id")
            payload = row.get("payload") or {}
            entry = payload.get("price") or _price_from_evidence(payload)
            consensus = payload.get("consensus") or {}
            signal = consensus.get("signal") or row.get("signal")
            symbol = row.get("symbol")
            if not symbol or not entry:
                continue

            if prices is not None:
                # The same key `due_symbols` asked for, or a legacy row's price
                # would be fetched under one name and looked up under another.
                later = prices.get(quote_symbol(row) or symbol)
            elif self.quote is not None:
                try:
                    later = self.quote(symbol)
                except Exception:
                    logger.debug("no quote for %s while scoring", symbol,
                                 exc_info=True)
                    continue
            else:
                later = None
            if later is None:
                # A delisted pair or a provider blip. Leave it for next time
                # rather than burning the sample on a price we never saw.
                continue

            realized = shadow_return(signal, float(entry), float(later))
            try:
                self.memory.record_thesis_outcome(
                    tid, symbol,
                    # A neutral call still resolves — the operator should see
                    # how often the table stood aside while the market moved —
                    # but with no direction to be right about.
                    realized if realized is not None else 0.0,
                    signal=signal, confidence=consensus.get("confidence"),
                    correct=None if realized is None else realized > 0,
                    notes=SHADOW_NOTE)
                scored += 1
            except Exception:
                logger.exception("could not record a shadow outcome for %s", tid)
        return scored


def _demo() -> None:
    assert shadow_return("bullish", 100.0, 110.0) == 0.10
    assert abs(shadow_return("bearish", 100.0, 90.0) - 0.10) < 1e-9
    assert shadow_return("neutral", 100.0, 150.0) is None
    assert shadow_return("bullish", 0.0, 10.0) is None
    assert is_shadow({"notes": SHADOW_NOTE})
    assert not is_shadow({"notes": None})
    print("shadow self-check passed")


if __name__ == "__main__":
    _demo()
