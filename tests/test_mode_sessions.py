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
