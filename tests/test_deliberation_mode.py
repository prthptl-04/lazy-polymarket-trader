"""A live panel must not show paper deliberations.

The Robinhood page splits live from paper down a hard vertical line — the
divider is load-bearing, because a simulated result read as a real one is the
worst mistake this dashboard can make. But the round-table panels on the LIVE
side showed every deliberation regardless of which side of the house executed
it, so the split stopped at the numbers and never reached the reasoning.

A deliberation does not carry a mode of its own: it happens before execution
decides anything. Its mode is the mode of the trade it produced, which is what
the join below recovers.

An undecided deliberation — one that never reached a venue — is NOT live. It is
reported as undecided, because guessing would put a paper argument under a live
heading, which is the thing being fixed.
"""

import pytest

from dashboard.runtime import DashboardRuntime
from memory.store import MemoryStore


def _store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "d.db"))


def _deliberated(store, tid, symbol="AAPL"):
    store.save_deliberation(thesis_id=tid, symbol=symbol, asset_class="equity",
                            status="complete", signal="bullish", confidence=70,
                            payload={"opinions": [], "tally": {}})


def _traded(store, tid, mode, symbol="AAPL"):
    store.record_closed_trade({
        "symbol": symbol, "asset_class": "equity", "realized_usd": 1.0,
        "realized_return": 0.01, "quantity": 1, "entry_price": 100.0,
        "exit_price": 101.0, "reason": "target", "venue": "robinhood",
        "mode": mode, "opened_at": 1.0, "closed_at": 2.0, "held_seconds": 1.0,
        "thesis_id": tid,
    })


def test_a_deliberation_takes_the_mode_of_the_trade_it_produced(tmp_path):
    store = _store(tmp_path)
    _deliberated(store, "t1"); _traded(store, "t1", "paper")
    rows = DashboardRuntime(memory=store).deliberations()
    assert rows[0]["mode"] == "paper"


def test_an_undecided_deliberation_is_not_called_live(tmp_path):
    """It reached no venue, so it belongs to neither side. Guessing would put a
    paper argument under a live heading."""
    store = _store(tmp_path)
    _deliberated(store, "t1")
    rows = DashboardRuntime(memory=store).deliberations()
    assert rows[0]["mode"] is None


def test_the_live_panel_shows_only_live_deliberations(tmp_path):
    store = _store(tmp_path)
    _deliberated(store, "paper1", "AAPL"); _traded(store, "paper1", "paper", "AAPL")
    _deliberated(store, "live1", "MSFT"); _traded(store, "live1", "live", "MSFT")
    _deliberated(store, "undecided1", "TSLA")

    live = DashboardRuntime(memory=store).deliberations(mode="live")
    assert [r["symbol"] for r in live] == ["MSFT"]


def test_the_paper_panel_shows_paper_and_undecided(tmp_path):
    """Undecided work belongs on the paper side: that is where the fund is
    actually running, and hiding it entirely would make the page look idle
    while the committee is deliberating."""
    store = _store(tmp_path)
    _deliberated(store, "paper1", "AAPL"); _traded(store, "paper1", "paper", "AAPL")
    _deliberated(store, "live1", "MSFT"); _traded(store, "live1", "live", "MSFT")
    _deliberated(store, "undecided1", "TSLA")

    paper = DashboardRuntime(memory=store).deliberations(mode="paper")
    assert sorted(r["symbol"] for r in paper) == ["AAPL", "TSLA"]


def test_no_filter_still_returns_everything(tmp_path):
    store = _store(tmp_path)
    _deliberated(store, "a", "AAPL"); _deliberated(store, "b", "MSFT")
    assert len(DashboardRuntime(memory=store).deliberations()) == 2


def test_the_live_panel_is_empty_while_nothing_trades_live(tmp_path):
    """The honest state today: the live gate refuses everything, so there are
    no live deliberations. An empty panel that says why beats a full one that
    is lying about which side of the house it is showing."""
    store = _store(tmp_path)
    for i in range(5):
        _deliberated(store, f"t{i}", "AAPL"); _traded(store, f"t{i}", "paper", "AAPL")
    assert DashboardRuntime(memory=store).deliberations(mode="live") == []
