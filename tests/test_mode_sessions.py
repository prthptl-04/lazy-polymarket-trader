"""Per-mode trading sessions: paper and live switch independently.

- Blind: the mode switch must NEVER block an exit. Switching paper off while a
  paper position is open, and finding you cannot close it, is the worst failure
  this control can have.
- Edge: an unknown mode, a venue with no adapter of that mode, and persistence
  across a restart.
"""

from datetime import datetime

import pytest

from dashboard.runtime import DashboardRuntime
from memory.store import MemoryStore
from trading.sessions import EASTERN
from trading.venues.base import OrderRequest
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter


MOMENT = datetime(2026, 9, 14, 11, 0, tzinfo=EASTERN)   # a Monday, market open


def _order(side: str = "buy") -> OrderRequest:
    # `is_close` is derived: a sell IS the close. Nothing to pass.
    return OrderRequest(
        symbol="AAPL", side=side, asset_class="equity",
        order_type="market", notional_usd=100.0, client_order_id="c1",
    )


def _router() -> VenueRouter:
    return VenueRouter(adapters=[PaperVenue()])


def test_paper_off_blocks_opening():
    r = _router()
    r.set_mode_enabled("paper", "paper", False)
    decision = r.evaluate(_order(), MOMENT)
    assert not decision.allowed and decision.gate == "mode_session"


def test_paper_off_still_lets_you_out():
    """A switch that traps an open position is worse than no switch."""
    r = _router()
    r.set_mode_enabled("paper", "paper", False)
    assert r.evaluate(_order("sell"), MOMENT).allowed


def test_the_two_modes_are_independent():
    r = _router()
    r.set_mode_enabled("paper", "live", False)      # live off
    assert r.evaluate(_order(), MOMENT).allowed     # paper still opens


def test_unknown_mode_is_refused_not_silently_stored():
    with pytest.raises(ValueError):
        _router().set_mode_enabled("paper", "margin", True)


def test_mode_of_treats_an_undeclared_adapter_as_live():
    class Mystery:
        name = "mystery"
        def supports(self, asset_class): return True
    assert VenueRouter.mode_of(Mystery()) == "live"
    assert VenueRouter.mode_of(PaperVenue()) == "paper"


def _runtime(tmp_path, router):
    class Fund: pass
    class Sched: pass
    fund, sched = Fund(), Sched()
    fund.router = router
    sched.fund = fund
    return DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "m.db")),
                            fund_scheduler=sched)


def test_live_side_reports_that_no_adapter_is_attached(tmp_path):
    """Otherwise a live GO promises something the process cannot do."""
    modes = _runtime(tmp_path, _router()).venue_modes()
    assert modes["paper"]["paper"]["attached"] is True
    assert modes["paper"]["live"]["attached"] is False


def test_mode_choice_survives_a_restart(tmp_path):
    rt = _runtime(tmp_path, _router())
    assert rt.set_venue_mode("paper", "paper", False)["ok"]

    fresh = _runtime(tmp_path, _router())
    fresh.restore_venue_sessions()
    assert fresh.venue_modes()["paper"]["paper"]["on"] is False


def test_unknown_venue_is_rejected(tmp_path):
    result = _runtime(tmp_path, _router()).set_venue_mode("nasdaq", "live", True)
    assert result["ok"] is False and "unknown venue" in result["reason"]


# ---------- two execution venues ----------

class _Live:
    """A live adapter. Declares itself, the way trading.live_gate expects."""
    name = "robinhood"
    is_live = True
    def supports(self, asset_class): return asset_class in ("equity", "crypto")


class _Gate:
    def __init__(self, allowed): self.allowed = allowed
    def evaluate(self, adapter):
        return type("V", (), {"allowed": self.allowed, "reason": "test"})()
    def status(self): return {}


def _two_venue_router(*, gate_open: bool, live_on: bool = True) -> VenueRouter:
    r = VenueRouter(adapters=[PaperVenue(), _Live()], live_gate=_Gate(gate_open))
    r.set_mode_enabled("robinhood", "live", live_on)
    return r


def test_live_is_only_selected_when_the_gate_and_the_switch_both_say_yes():
    assert _two_venue_router(gate_open=True).venue_for("equity").name == "robinhood"
    # Either one saying no is enough to keep execution on paper.
    assert _two_venue_router(gate_open=False).venue_for("equity").name == "paper"
    assert _two_venue_router(gate_open=True, live_on=False).venue_for("equity").name == "paper"


def test_a_switch_cannot_promote_itself_past_the_checklist():
    """The whole point of rule #13: the operator's intent is not the authority."""
    r = _two_venue_router(gate_open=False)
    r.set_mode_enabled("robinhood", "live", True)
    assert r.venue_for("equity").name == "paper"


def test_no_gate_attached_refuses_live():
    r = VenueRouter(adapters=[PaperVenue(), _Live()])      # live_gate is None
    assert r.venue_for("equity").name == "paper"


def test_a_close_goes_back_to_the_venue_that_opened_it():
    """Routing a live exit to paper would close it in our books only."""
    r = _two_venue_router(gate_open=True)
    r.opened_at["AAPL"] = "paper"
    assert r.venue_for("equity", symbol="AAPL", is_close=True).name == "paper"
    # ...even after live is switched off, a live position still exits live.
    r.opened_at["MSFT"] = "robinhood"
    r.set_mode_enabled("robinhood", "live", False)
    assert r.venue_for("equity", symbol="MSFT", is_close=True).name == "robinhood"


@pytest.mark.asyncio
async def test_place_records_where_a_position_lives():
    r = VenueRouter(adapters=[PaperVenue(quote_source=None)])
    r.adapters[0].set_quote("AAPL", bid=100.0, ask=100.1)
    await r.place(_order(), MOMENT)
    assert r.opened_at["AAPL"] == "paper"


@pytest.mark.asyncio
async def test_a_partial_exit_does_not_forget_where_the_rest_lives():
    """Forgetting on the first sell would route the remainder by mode rules."""
    r = VenueRouter(adapters=[PaperVenue(quote_source=None)])
    r.adapters[0].set_quote("AAPL", bid=100.0, ask=100.1)
    await r.place(_order(), MOMENT)
    await r.place(OrderRequest(symbol="AAPL", side="sell", asset_class="equity",
                               order_type="market", quantity=0.2,
                               client_order_id="c2"), MOMENT)
    assert r.opened_at["AAPL"] == "paper"
