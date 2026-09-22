"""The fund's live state, so a restart is not a silent deletion.

Five pieces of load-bearing state used to live in memory only. Losing them was
not a clean failure — each failed in a different, quiet, wrong direction:

**The record.** A position open across a restart was orphaned: the book no
longer held it, so `PositionBook.close` could never run, so no `closed_trades`
row and no `thesis_outcomes` row could ever be written for it. Only trades that
opened AND closed inside one process lifetime reached the record. Restart is a
hazard in wall-clock time, so it censors long holds — and under the fund's
2xATR/3xATR geometry winners take about 31% longer than losers, so it censored
WINNERS. A daemon restarted once per mean trade life records roughly a 34% win
rate and -0.14R on a strategy with no edge at all.

**The kill switch.** A restart re-based the day's opening equity to the lower
figure, so a -$50 day silently became a $100 budget, and a tripped day resumed
trading.

**The PDT ledger.** Three day trades used became zero, and the fourth in five
business days is a 90-day restriction on a real account.

Design, and the one thing that makes it safe: **the book and the venue are two
projections of ONE set of position numbers.** `PaperVenue` does not store its
own positions. If it did, a restore could bring back a book holding what the
venue does not — every exit rejected "cannot sell X: holding 0" — which is the
phantom-position bug, mirrored. They cannot disagree at startup because there
is nothing to disagree with.

Cash is stored rather than derived, because deriving it loses realised P&L, and
reconstructing that from `closed_trades` would bake in the known quantity seam
`FundLoop` already warns about. The identity

    starting_cash - sum(qty x entry) + realized == cash

holds exactly, and is checked on restore as proof the two halves agree.

Stored as one `agent_state` blob rather than a table: the hard requirement is
ATOMICITY across five objects, and a blob is atomic in one statement. Nothing
queries open positions from SQL — the dashboard reads the book in memory — so a
table would buy queryability nobody uses, at the cost of a transaction
discipline `MemoryStore` does not have.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATE_AGENT = "fund"
STATE_KEY = "runtime_state"

# One integer, and not speculative generality: this is the fund's only copy of
# its live positions, and an UNREADABLE blob must be distinguishable from an
# ABSENT one. Absent means "flat book, trade freely"; unreadable means
# "something is wrong, do not open new risk".
STATE_VERSION = 1

# The cash identity is exact in algebra and drifts in binary — measured at
# ~8e-12 over a few hundred fills. An equality check would cry wolf and then be
# deleted. This is orders of magnitude above the drift and orders of magnitude
# below the smallest real inconsistency, which is one whole position.
CASH_TOLERANCE_RATIO = 1e-6


def snapshot(*, book, venue, kill_switch=None, pdt=None, today=None,
             pending_plans=None) -> dict:
    """Everything that must survive, in one blob.

    `pending_plans` is the exit plan each RESTING entry was sized against,
    keyed by client_order_id. A resting order outlives the process, so its plan
    has to as well — without it the fill arrives with no stop and no route back
    into the book, and `_venue_rows` below then discards the position on the
    restart after that while its cash stays spent.
    """
    state: dict[str, Any] = {
        "version": STATE_VERSION,
        "saved_at": time.time(),
        "positions": book.snapshot() if book is not None else [],
        "pending_plans": dict(pending_plans or {}),
    }
    if venue is not None and hasattr(venue, "snapshot"):
        # Only the venue's CASH fields. Its own `positions` are deliberately
        # discarded here: the book's rows are the one set of position numbers,
        # and letting the venue's flatter copy through would overwrite the plan
        # and `opened_at` with a row that has neither — the two-sources-of-truth
        # failure this module exists to prevent, at the one line where it could
        # actually happen.
        venue_state = venue.snapshot()
        state.update({k: v for k, v in venue_state.items() if k != "positions"})
    if kill_switch is not None:
        state["kill_switch"] = kill_switch.snapshot()
    if pdt is not None:
        state["pdt"] = pdt.snapshot(today=today or _today())
    return state


def restore(memory, *, book, venue, router=None, kill_switch=None, pdt=None) -> list[str]:
    """Rebuild the fund's state. Returns warnings; never raises.

    A fund that refuses to start because its blob is odd is worse than one that
    starts flat and says so — the same stance `build_fund` takes when it
    returns None.
    """
    warnings: list[str] = []
    if memory is None:
        return warnings
    try:
        state = memory.get(STATE_AGENT, STATE_KEY, None)
    except Exception:
        logger.exception("could not read fund state")
        return ["fund state could not be read; starting flat"]
    if not state:
        return warnings
    if not isinstance(state, dict):
        return ["fund state is not a mapping; starting flat"]

    version = state.get("version")
    if version != STATE_VERSION:
        warnings.append(
            f"fund state is version {version!r}, expected {STATE_VERSION}; "
            "starting flat rather than guessing at its shape")
        return warnings

    # Positions FIRST: the venue is seeded from the same rows, so the two can
    # never come back holding different things.
    if book is not None:
        warnings.extend(book.restore(state.get("positions")))
    if venue is not None and hasattr(venue, "restore"):
        venue.restore({**state, "positions": _venue_rows(book)})
        warnings.extend(_check_cash(venue))
    if kill_switch is not None:
        kill_switch.restore(state.get("kill_switch"))
    if pdt is not None:
        pdt.restore(state.get("pdt"))
    if router is not None and book is not None:
        # DERIVED, not a second stored copy. Two copies can disagree about
        # where a position lives, and that disagreement routes an exit to the
        # wrong broker.
        router.opened_at.update(
            {p.symbol: p.venue for p in book.positions.values() if p.venue})
    return warnings


def restore_pending_plans(memory, fund) -> None:
    """Hand a freshly built FundLoop the plans for orders already resting.

    Separate from `restore` because the loop is constructed AFTER the book and
    the venue — it needs the router they produce. Never raises: a fund that
    refuses to start because one plan is odd is worse than one that starts and
    reports the orders it can no longer place a stop behind.
    """
    if memory is None or fund is None:
        return
    try:
        state = memory.get(STATE_AGENT, STATE_KEY, None) or {}
        plans = state.get("pending_plans") if isinstance(state, dict) else None
        fund._pending_plans = dict(plans) if isinstance(plans, dict) else {}
    except Exception:
        logger.exception("could not restore the pending exit plans")
        fund._pending_plans = {}


def save(memory, state: dict) -> None:
    if memory is None:
        return
    try:
        memory.put(STATE_AGENT, STATE_KEY, state)
    except Exception:
        logger.exception("could not persist fund state")


def _venue_rows(book) -> list[dict]:
    """The venue's view of the same positions the book just restored."""
    if book is None:
        return []
    return [{"symbol": p.symbol, "asset_class": p.asset_class,
             "quantity": p.quantity, "avg_price": p.entry_price}
            for p in book.positions.values()]


def _check_cash(venue) -> list[str]:
    """Does the restored account add up?

    Never REPAIRS a mismatch. Adjusting cash to satisfy the identity converts a
    detectable inconsistency into an undetectable fabrication.
    """
    try:
        positions = list(venue._positions.values())
        derived = (venue.starting_cash_usd
                   - sum(p.quantity * p.avg_price for p in positions)
                   + venue.realized_pnl_usd)
    except Exception:
        return []
    tolerance = CASH_TOLERANCE_RATIO * max(1.0, venue.starting_cash_usd)
    if abs(derived - venue.cash_usd) > tolerance:
        return [f"restored cash ${venue.cash_usd:,.2f} does not match the "
                f"positions and realised P&L (${derived:,.2f}); the state is "
                "inconsistent and has NOT been adjusted to hide it"]
    return []


def _today():
    from datetime import datetime, timezone
    from trading.sessions import EASTERN
    return datetime.now(timezone.utc).astimezone(EASTERN).date()
