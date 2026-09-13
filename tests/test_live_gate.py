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
    m = MemoryStore(db_path=str(tmp_path / "g.db"))
    for i in range(trades):
        m.log_trade("fund", f"S{i}", "buy", 1.0, 0.5, True, True, "ok")
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
