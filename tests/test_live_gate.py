"""Rule #13 in the path the fund actually uses.

The gap this closes: CLAUDE.md #4 and #13 were enforced by
`trading/execution.py::Executor`, which belongs to the removed CLOB path. The
fund goes ThesisPipeline -> VenueRouter -> adapter, so PAPER_TRADING=true had
NO effect on it. Attaching a real venue would have placed real orders with no
gate whatsoever.

- Blind: refuses by DEFAULT. Missing env, missing memory, an exception while
  counting — every failure lands on "paper only".
- Blind: an unknown adapter is treated as LIVE. Assuming a new venue is
  harmless is how real money moves by accident.
- Blind: rule #13 also applies to CLOSES. "It's an exit" must not bypass it.
- Acceptance: a paper venue is always allowed, or condition 4 could never be met.
"""

from datetime import datetime

import pytest

from memory.store import MemoryStore
from trading.live_gate import LiveTradingGate, _is_live_venue
from trading.sessions import EASTERN
from trading.venues.base import OrderRequest
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter
from verification.criteria import (
    LIVE_APPROVAL_LESSON,
    MIN_PAPER_TRADES_FOR_LIVE,
    VerifiedOutcomeCriteria,
)

WED = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)


class _LiveVenue(PaperVenue):
    """A paper venue wearing a live badge, so tests never touch a broker."""
    def __post_init__(self):
        super().__post_init__()
        self.name = "robinhood"
        self.is_live = True
        self.can_trade = True


def _ready_memory(tmp_path, trades=MIN_PAPER_TRADES_FOR_LIVE):
    """The gate counts CLOSED PAPER ROUND TRIPS, not orders. An order that was
    accepted and never traded is not a trade — and extended-hours limit orders
    are exactly that, which made "fifty orders where nothing happened" the
    cheapest route to opening this gate."""
    m = MemoryStore(db_path=str(tmp_path / "g.db"))
    for i in range(trades):
        m.log_trade("fund", f"S{i}", "buy", 1.0, 0.5, True, True, "ok", filled=True)
        m.record_closed_trade({
            "symbol": f"S{i}", "asset_class": "equity", "mode": "paper",
            "entry_price": 100.0, "exit_price": 101.0, "quantity": 1.0,
            "realized_return": 0.01, "realized_usd": 1.0, "closed_at": float(i),
        })
    m.record_lesson("*", LIVE_APPROVAL_LESSON)
    return m


def _gate(memory=None, bankroll=1000.0, max_pos=10.0):
    return LiveTradingGate(memory=memory, bankroll_usd=bankroll,
                           criteria=VerifiedOutcomeCriteria(max_position_usd=max_pos))


# ---------------- paper is always fine ----------------

def test_paper_venue_is_always_allowed():
    """Blocking it would make condition 4 unreachable."""
    assert _gate().evaluate(PaperVenue(name="paper")).allowed


# ---------------- refuses by default ----------------

def test_live_venue_is_blocked_with_nothing_configured():
    v = _gate().evaluate(_LiveVenue())
    assert not v.allowed and "LIVE TRADING BLOCKED" in v.reason


def test_every_condition_must_hold(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    gate = _gate(memory=_ready_memory(tmp_path))
    assert gate.evaluate(_LiveVenue()).allowed          # all five met

    # Remove them one at a time.
    monkeypatch.setenv("PAPER_TRADING", "true")
    assert not gate.evaluate(_LiveVenue()).allowed


def test_missing_approval_lesson_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    m = MemoryStore(db_path=str(tmp_path / "g.db"))
    for i in range(MIN_PAPER_TRADES_FOR_LIVE):
        m.log_trade("fund", f"S{i}", "buy", 1.0, 0.5, True, True, "ok")
    v = _gate(memory=m).evaluate(_LiveVenue())
    assert "operator_approval_lesson" in v.failed


def test_too_few_paper_trades_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    v = _gate(memory=_ready_memory(tmp_path, trades=3)).evaluate(_LiveVenue())
    assert "paper_trades_recorded" in v.failed


def test_a_cap_larger_than_the_bankroll_is_not_a_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    v = _gate(memory=_ready_memory(tmp_path), bankroll=100.0,
              max_pos=5000.0).evaluate(_LiveVenue())
    assert "risk_caps_live_appropriate" in v.failed


def test_unknown_bankroll_refuses_rather_than_assuming(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    v = _gate(memory=_ready_memory(tmp_path), bankroll=0.0).evaluate(_LiveVenue())
    assert "risk_caps_live_appropriate" in v.failed


def test_an_unauthenticated_live_venue_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    v = _LiveVenue(); v.can_trade = False
    assert "venue_authenticated" in _gate(memory=_ready_memory(tmp_path)).evaluate(v).failed


def test_no_memory_means_no_live_trading(monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    v = _gate(memory=None).evaluate(_LiveVenue())
    assert not v.allowed


def test_a_counting_failure_lands_on_refusal(monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    class _Broken:
        def recent_trades(self, limit=0): raise RuntimeError("db gone")
        def recent_lessons(self, a, limit=0): raise RuntimeError("db gone")
    assert not _gate(memory=_Broken()).evaluate(_LiveVenue()).allowed


# ---------------- unknown adapters are live ----------------

def test_an_unknown_adapter_is_treated_as_live():
    """Assuming a new venue is harmless is how real money moves by accident."""
    class _Mystery:
        name = "some-new-broker"
    assert _is_live_venue(_Mystery()) is True


def test_paper_is_recognised_by_its_declaration():
    assert _is_live_venue(PaperVenue(name="paper")) is False


# ---------------- wired into the router ----------------

@pytest.mark.asyncio
async def test_router_blocks_a_live_order_before_the_venue():
    venue = _LiveVenue(starting_cash_usd=10_000.0, slippage_bps=0)
    venue.set_quote("AAPL", bid=99.9, ask=100.1)
    router = VenueRouter(adapters=[venue], live_gate=_gate())

    ack = await router.place(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=1), WED)
    assert not ack.accepted and "[live_trading]" in ack.error
    assert await venue.positions() == []


@pytest.mark.asyncio
async def test_rule_13_also_applies_to_closes():
    """'It's an exit' must not be a bypass for the live checklist."""
    venue = _LiveVenue()
    venue.set_quote("AAPL", bid=99.9, ask=100.1)
    router = VenueRouter(adapters=[venue], live_gate=_gate())
    ack = await router.place(
        OrderRequest(symbol="AAPL", side="sell", asset_class="equity", quantity=1), WED)
    assert not ack.accepted and "[live_trading]" in ack.error


@pytest.mark.asyncio
async def test_paper_orders_flow_normally_through_the_router():
    venue = PaperVenue(name="paper", starting_cash_usd=10_000.0, slippage_bps=0)
    venue.set_quote("AAPL", bid=99.9, ask=100.1)
    router = VenueRouter(adapters=[venue], live_gate=_gate())
    assert (await router.place(
        OrderRequest(symbol="AAPL", side="buy", asset_class="equity", quantity=1), WED)).accepted


def test_router_status_exposes_the_checklist():
    s = VenueRouter(adapters=[PaperVenue()], live_gate=_gate()).status(WED)
    assert s["live_gate"]["live_possible"] is False
    assert s["live_gate"]["required_paper_trades"] == MIN_PAPER_TRADES_FOR_LIVE


# ---------------- paper progress panel ----------------

def test_paper_progress_counts_orders_and_the_bar_separately(tmp_path):
    """This test used to seed seven graded ORDERS and assert the bar read 7.

    It was encoding the bug: an order that was accepted and never traded is not
    a paper trade, and counting it let the page read "ready for live" on orders
    that never happened. Rule #13's condition 4 is closed paper ROUND TRIPS.

    Both numbers are still reported, under names that say which is which.
    """
    from dashboard.runtime import build_runtime
    from memory.store import MemoryStore

    store = MemoryStore(db_path=str(tmp_path / "p.db"))
    for i in range(7):
        store.log_trade("fund", f"S{i}", "buy", 1.0, 0.5, True, True, "ok")
    store.log_trade("fund", "UGLY", "buy", 1.0, 0.5, True, False, "rejected")

    p = build_runtime(memory=store).paper_progress()
    assert p["graded_orders"] == 7              # the rejected one does not count
    assert p["total_trades_logged"] == 8
    assert p["graded_paper_trades"] == 0        # none of them closed
    assert p["pct_complete"] == 0.0
    assert p["required"] == MIN_PAPER_TRADES_FOR_LIVE


def test_paper_progress_is_empty_but_explicit_on_a_fresh_fund(tmp_path):
    from dashboard.runtime import build_runtime
    from memory.store import MemoryStore
    p = build_runtime(memory=MemoryStore(db_path=str(tmp_path / "p.db"))).paper_progress()
    assert p["graded_paper_trades"] == 0
    assert p["seats"] == [] and p["lessons_learned"] == 0
    assert p["shrink_fit"]["usable"] is False


def test_a_seat_counts_as_improving_only_if_calibrated_AND_beating_chance(tmp_path):
    """Beating chance while claiming 95% certainty is not skill."""
    from dashboard.runtime import DashboardRuntime
    from memory.store import MemoryStore

    rt = DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "p.db")))
    rt.scorecard = lambda: {"resolved": 10, "seats": [
        {"seat_id": "a", "samples": 10, "calibrated": True, "beats_coin_flip": True},
        {"seat_id": "b", "samples": 10, "calibrated": False, "beats_coin_flip": True},
        {"seat_id": "c", "samples": 10, "calibrated": True, "beats_coin_flip": False},
    ], "fit": {"usable": False}}
    rt.lessons = lambda limit=200: []
    assert rt.paper_progress()["seats_calibrated"] == 1
    assert rt.paper_progress()["seats_scored"] == 3


def test_overview_ships_the_paper_panel():
    from dashboard.pages import OVERVIEW_HTML
    assert 'id="paper"' in OVERVIEW_HTML
    assert 'id="seatperf"' in OVERVIEW_HTML
    assert "graded trades" in OVERVIEW_HTML
    # The empty state must explain how a seat is scored.
    assert "dissenter" in OVERVIEW_HTML


def test_paper_route_is_served(tmp_path):
    from fastapi.testclient import TestClient
    from dashboard.runtime import build_runtime
    from dashboard.server import create_app
    from memory.store import MemoryStore
    body = TestClient(create_app(build_runtime(
        memory=MemoryStore(db_path=str(tmp_path / "p.db"))))).get("/api/paper").json()
    assert body["required"] == MIN_PAPER_TRADES_FOR_LIVE


# ---------------- graduation checklist (display only) ----------------

def test_graduation_items_never_default_to_ok(tmp_path):
    """A checklist whose unknown boxes render as ticks is the hardcoded
    preflight repeated one layer down — and this one sits beside a live-money
    decision. Anything not measurable must read false."""
    gate = _gate(memory=MemoryStore(db_path=str(tmp_path / "grad.db")))
    items = gate.graduation()
    for item in items:
        if "not measurable" in item["detail"]:
            assert item["ok"] is False, item["label"]
    ids = {i["id"] for i in items}
    assert {"round_trips", "regimes", "stops_fired", "reconciled"} <= ids


def test_graduation_does_not_gate_anything(tmp_path):
    """The blocking set stays the five conditions rule #13 names. A gate that
    tightens itself past its own documentation is how the documentation stops
    being believed."""
    memory = _ready_memory(tmp_path)
    gate = _gate(memory=memory)
    # The rule-#13 condition this checklist could plausibly have hijacked is
    # satisfied...
    assert gate.status()["checks"]["paper_trades_recorded"] is True
    # ...while graduation items remain unmet. They inform; they do not block.
    assert any(not i["ok"] for i in gate.graduation())
    # status() carries the four conditions that do not depend on an adapter;
    # venue_authenticated is evaluated per venue inside evaluate(). Five in
    # total, and graduation adds none of them.
    assert set(gate.status()["checks"]) == {
        "env_paper_trading_false", "risk_caps_live_appropriate",
        "paper_trades_recorded", "operator_approval_lesson",
    }, "the blocking set must stay the conditions rule #13 names"
    assert "venue_authenticated" in gate.evaluate(_LiveVenue()).checks


def test_the_risk_system_is_untested_until_a_stop_has_fired(tmp_path):
    m = MemoryStore(db_path=str(tmp_path / "stops.db"))
    for i in range(6):
        m.record_closed_trade({"symbol": f"S{i}", "mode": "paper", "reason": "stop",
                               "entry_price": 100.0, "exit_price": 96.0, "stop": 96.0,
                               "quantity": 1.0, "realized_return": -0.04,
                               "realized_usd": -4.0, "closed_at": float(i)})
    item = next(i for i in _gate(memory=m).graduation() if i["id"] == "stops_fired")
    assert item["ok"] is True and "6 of 5" in item["detail"]
