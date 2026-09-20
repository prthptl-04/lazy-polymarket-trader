"""Venues the fund no longer trades.

Retirement is a declaration, not a deletion. The adapter, its tests and its
dashboard wiring all stay on disk; what changes is that the router refuses to
route to it and the UI renders its page as switched off. That ordering is
deliberate:

- **Deleting it would hide the decision.** A venue that silently vanishes looks
  like a bug six months later. A venue that says "retired, and here is why"
  is a decision someone can read and reverse.
- **Refusing at the router is refusing everywhere.** `VenueRouter.place` is the
  single chokepoint between a decision and a broker (rule #21). One check there
  covers the fund loop, exits, the cashout engine and anything added later,
  without each of them having to remember.

To bring one back: remove its entry here. Nothing else is switched off.
"""

from __future__ import annotations

RETIRED_VENUES: dict[str, str] = {
    "polymarket_us": (
        "Retired 2026-09-20. The fund outgrew prediction markets: the round "
        "table, the Kelly sizer and the session calendar are all built for "
        "instruments with a continuous price and a real exit, and a binary "
        "that settles has neither. Kalshi was researched as the replacement "
        "and rejected on its own evidence (docs/KALSHI_BTC_15M.md §14), which "
        "did not revive the case for Polymarket — that case was already gone. "
        "The fund trades Robinhood only: equities on weekdays, crypto at "
        "weekends."
    ),
}


def is_retired(venue_name: str) -> bool:
    return venue_name in RETIRED_VENUES


def retirement_reason(venue_name: str) -> str | None:
    return RETIRED_VENUES.get(venue_name)
