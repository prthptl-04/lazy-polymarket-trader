"""Disclosed trades by tracked public officials.

These are STOCK Act filings — public records the government publishes precisely
so anyone can read them. Reading them is legal and ordinary, and several
commercial products do nothing else. It is the opposite of insider information,
which by definition is material and NON-public.

Two refusals, both of which are what make this worth having.

**The lag is never hidden.** A Periodic Transaction Report is due within 45 days
of the trade. By the time it is public the position may be six weeks old, which
for a fund holding for days is most of a lifetime. Every line carries the age,
because "Pelosi bought NVDA" and "Pelosi bought NVDA six weeks ago" are
different claims and only the second one is true.

**No weight is applied.** Everything else here earns its weight by measurement:
seat weights need 30 scored calls, the confidence shrink needs 30 resolved
outcomes, post-mortem lessons need 30 before they are injected at all. A
hardcoded multiplier on "a politician bought it" would be the one unmeasured
edge in the system and the one nobody could falsify. This goes to the Catalyst
seat as evidence — that seat already owns insider flow, and a congressional
filing is the same kind of fact — and the committee decides what it is worth.

If it turns out to work, the machinery to prove it already exists: the calls
are recorded with the deliberation, and `roundtable.calibration` will score
them like anything else. That is the honest route to weighting it.

Source: OpenBB's `equity.ownership.government_trades`, which needs an FMP key
(free tier). Without one this degrades to a stated reason, same as every other
catalyst source.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from trading.catalysts import CatalystEvidence, _date, _rows

logger = logging.getLogger(__name__)

# STOCK Act: a periodic transaction report is due within 45 days of the trade.
DISCLOSURE_DEADLINE_DAYS = 45
# Filings older than this stop being a catalyst and start being history. Set
# beyond the deadline so a late-but-legal filing is still seen.
MAX_TRADE_AGE_DAYS = 75
# Distinct tracked filers on one name before it is worth calling a cluster.
CLUSTER_FILERS = 3

# The operator's list. Members of Congress file under the STOCK Act; the two
# non-members are here because they were asked for, and are handled honestly:
# Trump files as a federal official, and Aschenbrenner runs a private fund with
# no disclosure obligation at all — see `UNAVAILABLE_FILERS`.
TRACKED_FILERS: tuple[str, ...] = (
    "Nancy Pelosi",
    "Ro Khanna",
    "Josh Gottheimer",
    "Richard Blumenthal",
    "Michael McCaul",
    "Cleo Fields",
    "Donald Trump",
)

# Named so the absence is recorded rather than looking like a fetch that failed.
UNAVAILABLE_FILERS: dict[str, str] = {
    "Leopold Aschenbrenner": (
        "runs a private investment firm with no public disclosure obligation — "
        "there is no filing to track, and any 'portfolio' circulating for them "
        "is inference, not a record"
    ),
}


@dataclass
class PoliticalTradeFeed:
    """Fetches disclosed official trades for one symbol."""

    timeout_seconds: float = 12.0
    filers: tuple[str, ...] = TRACKED_FILERS
    _import: Optional[Callable[[], Any]] = None

    def _obb(self) -> Any:
        if self._import is not None:
            return self._import()
        from openbb import obb
        return obb

    async def trades_for(self, symbol: str) -> CatalystEvidence:
        try:
            obb = await asyncio.to_thread(self._obb)
        except ImportError as e:
            return CatalystEvidence(
                reason=f"OpenBB is not installed ({e}); political filings are optional")
        except Exception as e:
            return CatalystEvidence(reason=f"OpenBB failed to load: {type(e).__name__}")

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: obb.equity.ownership.government_trades(
                        symbol=symbol, chamber="all")),
                timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return CatalystEvidence(
                reason=f"the filings provider did not answer in "
                       f"{self.timeout_seconds:.0f}s")
        except Exception as e:
            reason = str(e).strip() or type(e).__name__
            return CatalystEvidence(
                reason=f"disclosed-trade data unavailable ({reason[:90]}). "
                       "This source needs an FMP API key (free tier).")

        note = summarise_political_trades(_rows(result), symbol, filers=self.filers)
        return CatalystEvidence(notes=(note,), available=True)


def summarise_political_trades(rows: list[dict], symbol: str, *,
                               filers: tuple[str, ...] = TRACKED_FILERS,
                               today: Optional[str] = None) -> str:
    """One derived line: who, which way, how large a band, and HOW LONG AGO."""
    ref = _date(today) or datetime.now(timezone.utc).date()
    wanted = {f.lower() for f in filers}

    hits: list[tuple[str, str, str, str, int]] = []
    for row in rows:
        who = str(row.get("representative") or row.get("name") or "").strip()
        if not who or not any(w in who.lower() for w in _surnames(wanted)):
            continue
        if str(row.get("symbol") or "").upper() != symbol.upper():
            continue
        traded = _date(row.get("transactionDate") or row.get("transaction_date"))
        if traded is None:
            continue
        age = (ref - traded).days
        if age < 0 or age > MAX_TRADE_AGE_DAYS:
            continue
        side = str(row.get("transactionType") or row.get("transaction_type") or "")
        verb = ("bought" if "purchase" in side.lower()
                else "sold" if "sale" in side.lower() else side.lower() or "traded")
        amount = str(row.get("amount") or "an undisclosed band").strip()
        hits.append((who, verb, amount, str(traded), age))

    if not hits:
        return (f"Disclosed official trades: no disclosed filings in {symbol} from "
                f"the tracked list in the last {MAX_TRADE_AGE_DAYS} days. Checked, "
                "not merely absent.")

    hits.sort(key=lambda h: h[4])
    lines = [f"{who} {verb} {amount} on {when} — disclosed {age} days ago"
             for who, verb, amount, when, age in hits[:5]]
    buyers = {h[0] for h in hits if h[1] == "bought"}

    out = ["Disclosed official trades in " + symbol + ": " + "; ".join(lines) + "."]
    if len(buyers) >= CLUSTER_FILERS:
        out.append(f"{len(buyers)} distinct tracked filers bought — a cluster, "
                   "which is the only pattern here with more signal than one "
                   "person's portfolio decision.")
    out.append(
        f"These are STOCK Act filings, due within {DISCLOSURE_DEADLINE_DAYS} days "
        "of the trade, so the position is already that old when it becomes "
        "public and the move it was betting on may have happened. Treat it as "
        "context, not as a reason on its own.")
    return " ".join(out)


def _surnames(wanted: set[str]) -> set[str]:
    """Match on surname: filings spell names inconsistently ('Nancy Pelosi',
    'Pelosi, Nancy', 'Hon. Nancy Pelosi')."""
    return {w.split()[-1] for w in wanted if w.split()}


def _demo() -> None:
    rows = [{"representative": "Nancy Pelosi", "symbol": "NVDA",
             "transactionType": "Purchase", "transactionDate": "2026-08-20",
             "amount": "$1,000,001 - $5,000,000"}]
    note = summarise_political_trades(rows, "NVDA", today="2026-09-21")
    assert "Pelosi" in note and "32 days ago" in note and "bought" in note, note
    assert "no disclosed" in summarise_political_trades([], "NVDA",
                                                        today="2026-09-21").lower()
    print("political_trades self-check passed")


if __name__ == "__main__":
    _demo()
