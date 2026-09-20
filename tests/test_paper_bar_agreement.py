"""The rule-#13 bar must mean one thing.

`dashboard.runtime.paper_progress()` counted rows in `trade_log` with
`grade_pass` — no filter on paper, on filled, or on a closed round trip.
`LiveTradingGate._graded_count()` counts closed PAPER round trips. Same key
name, `graded_paper_trades`, opposite semantics.

Reproduced before the fix, with sixty graded, LIVE, never-filled premarket
orders and no closed trades at all:

    dashboard paper_progress : {'graded_paper_trades': 60, 'required': 50}
    live gate                : 0
    closed_trades in db      : 0

The page read "ready for live" on sixty orders that never traded, on a live
venue. This is the number a human reads before flipping real money on, and it
is exactly the failure `_graded_count`'s own docstring says was fixed — it was
fixed in the gate and left in the display.

The orders number is still worth showing; it just is not this number. Reported
separately as `graded_orders`, so "60 orders graded, 0 closed round trips" is
legible instead of conflated.
"""

import pytest

from dashboard.runtime import DashboardRuntime
from memory.store import MemoryStore
from trading.live_gate import LiveTradingGate


def _store(tmp_path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "bar.db"))


def _graded_order(store, *, paper: bool, filled: bool, session="premarket"):
    """An order that passed the grader. Not necessarily a trade."""
    store.log_trade(agent_id="fund", market_id="AAPL", side="buy", size=10.0,
                    price=100.0, paper=paper, grade_pass=True, grade_reason="ok",
                    filled=filled, session=session, venue="robinhood")


def _closed_round_trip(store, *, mode: str, pnl: float = 5.0):
    """A position that was opened AND closed — what rule #13 actually counts."""
    store.record_closed_trade({
        "symbol": "AAPL", "asset_class": "equity", "realized_usd": pnl,
        "realized_return": pnl / 1000.0, "quantity": 1, "entry_price": 100.0,
        "exit_price": 100.0 + pnl, "reason": "target", "venue": "robinhood",
        "mode": mode, "opened_at": 1.0, "closed_at": 2.0, "held_seconds": 1.0,
    })


# ---------- the disagreement ----------

def test_graded_orders_that_never_traded_do_not_open_the_bar(tmp_path):
    """The reproduction. Sixty live, unfilled orders must read as zero."""
    store = _store(tmp_path)
    for _ in range(60):
        _graded_order(store, paper=False, filled=False)

    progress = DashboardRuntime(memory=store).paper_progress()

    assert progress["graded_paper_trades"] == 0
    assert progress["pct_complete"] == 0.0


def test_the_page_and_the_gate_report_the_same_number(tmp_path):
    """The invariant. Whatever the rule is, both must apply it."""
    store = _store(tmp_path)
    for _ in range(60):
        _graded_order(store, paper=False, filled=False)
    for _ in range(7):
        _closed_round_trip(store, mode="paper")

    progress = DashboardRuntime(memory=store).paper_progress()
    gate = LiveTradingGate(memory=store, bankroll_usd=10_000.0)

    assert progress["graded_paper_trades"] == gate.graded_paper_trades() == 7
    assert progress["graded_paper_trades"] == gate.status()["graded_paper_trades"]


def test_a_live_round_trip_does_not_count_toward_the_paper_bar(tmp_path):
    """The bar exists to earn the right to trade live. Live trades cannot
    be what earns it."""
    store = _store(tmp_path)
    _closed_round_trip(store, mode="paper")
    _closed_round_trip(store, mode="live")

    assert DashboardRuntime(memory=store).paper_progress()["graded_paper_trades"] == 1


def test_a_round_trip_with_no_recorded_mode_does_not_count(tmp_path):
    """Unknown resolves against the operator, same stance as the gate."""
    store = _store(tmp_path)
    _closed_round_trip(store, mode=None)

    assert DashboardRuntime(memory=store).paper_progress()["graded_paper_trades"] == 0


# ---------- the orders number survives, correctly labelled ----------

def test_graded_orders_are_still_reported_separately(tmp_path):
    """Sixty orders that produced no round trip is a real and useful fact —
    it just is not progress toward the bar. Conflating them is what broke this;
    dropping them would hide it."""
    store = _store(tmp_path)
    for _ in range(60):
        _graded_order(store, paper=False, filled=False)

    progress = DashboardRuntime(memory=store).paper_progress()

    assert progress["graded_orders"] == 60
    assert progress["graded_paper_trades"] == 0
    assert progress["total_trades_logged"] == 60


def test_pct_complete_tracks_round_trips_not_orders(tmp_path):
    store = _store(tmp_path)
    for _ in range(25):
        _closed_round_trip(store, mode="paper")
    for _ in range(100):
        _graded_order(store, paper=True, filled=True)

    progress = DashboardRuntime(memory=store).paper_progress()

    assert progress["graded_paper_trades"] == 25
    assert progress["pct_complete"] == 50.0, "25 of 50, not 100 of 50"


def test_pct_complete_is_capped_at_one_hundred(tmp_path):
    store = _store(tmp_path)
    for _ in range(80):
        _closed_round_trip(store, mode="paper")

    assert DashboardRuntime(memory=store).paper_progress()["pct_complete"] == 100.0


# ---------- degradation ----------

def test_an_unreadable_store_reports_zero_rather_than_a_number(tmp_path):
    """Refuse by default: a counting failure must not read as progress."""
    class _Broken:
        def closed_trades(self, **kw): raise RuntimeError("db gone")
        def recent_trades(self, **kw): raise RuntimeError("db gone")
        def recent_lessons(self, *a, **kw): return []
        def resolved_outcomes(self, **kw): return []
        def recent_deliberations(self, **kw): return []

    progress = DashboardRuntime(memory=_Broken()).paper_progress()
    assert progress["graded_paper_trades"] == 0
    assert progress["pct_complete"] == 0.0
