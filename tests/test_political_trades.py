"""Disclosed trades by tracked public officials, as dated evidence.

These are STOCK Act filings: public records the government publishes precisely
so anyone can read them. Tracking them is legal, ordinary, and the basis of
several commercial products. It is not insider information — insider trading
means material NON-public information, and this is the opposite by definition.

Two things this module refuses to do, both of which would make it worse than
nothing.

**It does not hide the lag.** A Periodic Transaction Report is due within 45
days of the trade. By the time it is public the position is up to 45 days old,
which for a fund holding for days is most of a lifetime. Every note carries the
delay, because "Pelosi bought NVDA" and "Pelosi bought NVDA six weeks ago" are
different claims and only the second is true.

**It does not apply a weight.** Everything else in this fund earns its weight
by measurement — seat weights need 30 scored calls, the confidence shrink needs
30 resolved outcomes, lessons need 30 before they are injected. A hardcoded
multiplier on "a politician bought it" would be the single unmeasured edge in
the system, and the one nobody could ever falsify. It goes to the Catalyst seat
as evidence, which already owns insider flow, and the committee decides what it
is worth.
"""

import pytest

from trading.political_trades import (
    DISCLOSURE_DEADLINE_DAYS, TRACKED_FILERS, PoliticalTradeFeed,
    summarise_political_trades,
)


def _row(name="Nancy Pelosi", symbol="NVDA", tx="Purchase",
         date="2026-08-20", disclosed="2026-09-18", amount="$1,000,001 - $5,000,000"):
    return {"representative": name, "symbol": symbol, "transactionType": tx,
            "transactionDate": date, "disclosureDate": disclosed, "amount": amount}


def _note(rows, symbol="NVDA", today="2026-09-21"):
    return summarise_political_trades(rows, symbol, today=today)


# ---------------------------------------------------------------- the lag

def test_the_note_states_how_old_the_trade_actually_is():
    """The single most important fact about this data, and the one a headline
    always drops."""
    note = _note([_row()])
    assert "32 days ago" in note


def test_a_filing_at_the_deadline_is_still_reported_with_its_age():
    note = _note([_row(date="2026-08-07", disclosed="2026-09-20")])
    assert "45 days ago" in note


def test_the_deadline_is_stated_so_the_lag_is_not_read_as_a_delay_we_chose():
    note = _note([_row()])
    assert str(DISCLOSURE_DEADLINE_DAYS) in note


# ---------------------------------------------------------------- content

def test_only_the_symbol_under_debate_is_reported():
    rows = [_row(symbol="NVDA"), _row(symbol="TSLA")]
    note = _note(rows, symbol="NVDA")
    assert "NVDA" in note and "TSLA" not in note


def test_a_purchase_and_a_sale_are_not_conflated():
    buy = _note([_row(tx="Purchase")])
    sell = _note([_row(tx="Sale")])
    assert "bought" in buy.lower() and "sold" in sell.lower()


def test_the_filer_is_named():
    assert "Pelosi" in _note([_row(name="Nancy Pelosi")])


def test_the_disclosed_size_band_is_carried_not_a_point_estimate():
    """Filings report a RANGE. Reporting a midpoint as a figure would invent a
    number the filing never stated."""
    note = _note([_row(amount="$1,000,001 - $5,000,000")])
    assert "1,000,001" in note and "5,000,000" in note


def test_several_filers_on_one_name_is_the_interesting_case():
    rows = [_row(name="Nancy Pelosi"), _row(name="Ro Khanna"),
            _row(name="Josh Gottheimer")]
    note = _note(rows)
    assert "3" in note and "cluster" in note.lower()


def test_nothing_disclosed_says_so_rather_than_staying_silent():
    """Silence and "we did not look" must not render identically."""
    assert "no disclosed" in _note([]).lower()


def test_an_untracked_filer_is_ignored():
    """The watchlist is the point. Every member of Congress files; the signal
    the operator asked for is these specific people."""
    assert "no disclosed" in _note([_row(name="Someone Unknown")]).lower()


def test_a_stale_filing_is_dropped_rather_than_presented_as_news():
    rows = [_row(date="2025-01-05", disclosed="2025-02-01")]
    assert "no disclosed" in _note(rows).lower()


def test_the_tracked_list_contains_the_named_officials():
    lowered = {n.lower() for n in TRACKED_FILERS}
    for who in ("pelosi", "khanna", "gottheimer", "blumenthal", "mccaul", "fields"):
        assert any(who in n for n in lowered), who


# ---------------------------------------------------------------- degrading

def test_no_provider_degrades_to_a_stated_reason():
    import asyncio
    feed = PoliticalTradeFeed(_import=lambda: (_ for _ in ()).throw(ImportError("no openbb")))
    ev = asyncio.run(feed.trades_for("NVDA"))
    assert ev.notes == () and "not installed" in ev.reason.lower()


def test_a_gated_provider_degrades_to_a_stated_reason():
    import asyncio

    class _Obb:
        class equity:
            class ownership:
                @staticmethod
                def government_trades(**kw):
                    raise RuntimeError("FMP key required")

    ev = asyncio.run(PoliticalTradeFeed(_import=lambda: _Obb).trades_for("NVDA"))
    assert ev.available is False
    assert "key" in ev.reason.lower() or "fmp" in ev.reason.lower()
