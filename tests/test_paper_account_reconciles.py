"""The paper account must be able to prove its own cash.

Measured on 2026-09-22 at 03:45, after the first crypto fills of the run:

    starting_cash   500.00
    cash            454.5263
    realized         -1.7237
    positions        none

    500.00 - 43.75 - 1.7237 = 454.5263    to the cent

The -1.7237 is a complete round trip and is correct. The 43.75 is not: one
position was debited from cash and then disappeared with no sale and no
realised entry. The venue had been carrying that gap across four restarts,
presenting it as a balance somebody had chosen.

It went unnoticed for hours for two reasons, and both are fixed here rather
than the symptom:

  1. Nothing ever checked the identity the snapshot docstring has always
     claimed held: `starting - sum(qty x avg) + realized == cash`.
  2. `fills` was not persisted, so by the time anyone looked, every fill that
     produced the cash had been discarded at the previous restart. The books
     could not be audited even in principle.

`reconcile()` reports the gap and REPAIRS NOTHING. A reconciliation that
adjusts the cash to match the book is not a reconciliation, and the gap is the
only evidence of what happened.
"""

import asyncio

import pytest

from trading.venues.base import OrderRequest
from trading.venues.paper import FILL_HISTORY, PaperVenue


def _venue(cash=500.0):
    v = PaperVenue(starting_cash_usd=cash)
    v.set_quote("LINK-USD", bid=12.71, ask=12.95)
    return v


async def _buy(v, notional=43.75, cid="b1", symbol="LINK-USD"):
    return await v.place_order(OrderRequest(
        symbol=symbol, side="buy", asset_class="crypto", order_type="market",
        notional_usd=notional, client_order_id=cid))


# ---------- the identity ----------

def test_a_fresh_account_reconciles():
    assert _venue().reconcile() is None


def test_an_account_reconciles_after_a_buy():
    v = _venue()
    asyncio.run(_buy(v))
    assert v.reconcile() is None


def test_an_account_reconciles_after_a_round_trip():
    v = _venue()
    asyncio.run(_buy(v))
    qty = v._positions["LINK-USD"].quantity
    v.set_quote("LINK-USD", bid=12.30, ask=12.40)
    asyncio.run(v.place_order(OrderRequest(
        symbol="LINK-USD", side="sell", asset_class="crypto",
        order_type="market", quantity=qty, client_order_id="s1")))
    assert v._positions == {}
    assert v.reconcile() is None
    assert v.realized_pnl_usd < 0


def test_the_observed_gap_is_detected():
    """The exact shape of the live failure: cash spent, position gone, no
    sale, no realised entry."""
    v = _venue()
    asyncio.run(_buy(v, notional=43.75))
    v._positions.clear()                       # what actually happened
    gap = v.reconcile()
    assert gap is not None
    assert "-43.75" in gap or "43.75" in gap
    assert "gap" in gap


def test_reconcile_reports_and_never_repairs():
    """The gap is the evidence. Erasing it to make the numbers agree would
    destroy the only record that anything went wrong."""
    v = _venue()
    asyncio.run(_buy(v))
    before = v.cash_usd
    v._positions.clear()
    v.reconcile()
    v.reconcile()
    assert v.cash_usd == before
    assert v.realized_pnl_usd == 0.0
    assert v.reconcile() is not None, "still broken, still saying so"


def test_floating_point_noise_is_not_a_gap():
    """A cent of tolerance. Reporting rounding as theft would train everyone
    to ignore the one message that must never be ignored."""
    v = _venue()
    asyncio.run(_buy(v))
    v.cash_usd += 0.002
    assert v.reconcile() is None
    v.cash_usd += 0.5
    assert v.reconcile() is not None


# ---------- the audit trail survives a restart ----------

def test_fills_are_persisted():
    v = _venue()
    asyncio.run(_buy(v))
    assert v.snapshot()["fills"], "the fills that made the cash must be stored"
    w = PaperVenue(starting_cash_usd=500.0)
    w.restore(v.snapshot())
    assert len(w.fills) == 1
    assert w.fills[0]["symbol"] == "LINK-USD"
    assert w.reconcile() is None


def test_the_fill_history_is_bounded():
    """One long-lived account must not grow the state row without limit."""
    v = _venue()
    v.fills = [{"symbol": "X", "side": "buy", "quantity": 1, "price": 1}
               for _ in range(FILL_HISTORY * 3)]
    assert len(v.snapshot()["fills"]) == FILL_HISTORY


def test_a_restore_that_does_not_reconcile_is_logged(caplog):
    """A restart is exactly where a position goes missing while the cash that
    bought it does not. A silent restore is how such a gap survives."""
    v = _venue()
    asyncio.run(_buy(v))
    snap = v.snapshot()
    snap["positions"] = []                     # the corruption, as observed
    w = PaperVenue(starting_cash_usd=500.0)
    with caplog.at_level("ERROR"):
        w.restore(snap)
    assert any("does not reconcile" in r.message for r in caplog.records)


# ---------- the cycle asks ----------

def test_the_cycle_reconciles_cash_every_pass():
    import inspect

    from trading.fund import FundLoop
    src = inspect.getsource(FundLoop.run_cycle)
    assert "_reconcile_cash" in src


def test_a_gap_is_an_error_not_a_note():
    """Money that moved without inventory moving with it is the one thing that
    must never be absorbed quietly."""
    from datetime import datetime

    from zoneinfo import ZoneInfo

    from trading.fund import CycleReport, FundLoop

    class _Adapter:
        name = "paper"
        def reconcile(self): return "cash $1.00 but the book implies $2.00; gap $-1.00"

    class _Router:
        adapters = [_Adapter()]

    fund = object.__new__(FundLoop)
    fund.router = _Router()
    report = CycleReport(moment=datetime(2026, 9, 22, 3, 45,
                                         tzinfo=ZoneInfo("America/New_York")),
                         session="crypto_only")
    FundLoop._reconcile_cash(fund, report)
    assert len(report.errors) == 1
    assert "does not reconcile" in report.errors[0]
    assert report.notes == []


def test_a_venue_that_cannot_reconcile_is_skipped():
    """A live adapter's books live at the broker."""
    from datetime import datetime

    from zoneinfo import ZoneInfo

    from trading.fund import CycleReport, FundLoop

    class _Router:
        adapters = [object()]

    fund = object.__new__(FundLoop)
    fund.router = _Router()
    report = CycleReport(moment=datetime(2026, 9, 22, 3, 45,
                                         tzinfo=ZoneInfo("America/New_York")),
                         session="crypto_only")
    FundLoop._reconcile_cash(fund, report)
    assert report.errors == []


# ---------- an investigated gap is booked, not erased ----------

def test_absorb_gap_books_the_amount_without_moving_money():
    v = _venue()
    asyncio.run(_buy(v, notional=43.75))
    v._positions.clear()
    cash, realized = v.cash_usd, v.realized_pnl_usd

    amount = v.absorb_gap("position vanished with no sale; fills not retained")
    assert amount == pytest.approx(-43.75, abs=0.01)
    assert v.cash_usd == cash, "the money does not move"
    assert v.realized_pnl_usd == realized, "it is not a trading loss"
    assert v.unexplained_usd == pytest.approx(-43.75, abs=0.01)
    assert v.reconcile() is None, "the books agree, with the gap NAMED"


def test_the_reason_survives_and_is_visible():
    v = _venue()
    asyncio.run(_buy(v))
    v._positions.clear()
    v.absorb_gap("investigated 2026-09-22, cause not found")
    snap = v.snapshot()
    assert snap["unexplained_usd"] == pytest.approx(-43.75, abs=0.01)
    assert "not found" in snap["unexplained_reason"]
    w = PaperVenue(starting_cash_usd=500.0)
    w.restore(snap)
    assert w.unexplained_usd == pytest.approx(-43.75, abs=0.01)
    assert "not found" in w.unexplained_reason
    assert w.reconcile() is None


def test_booking_requires_a_reason():
    v = _venue()
    asyncio.run(_buy(v))
    v._positions.clear()
    with pytest.raises(ValueError):
        v.absorb_gap("")
    with pytest.raises(ValueError):
        v.absorb_gap("   ")
    assert v.unexplained_usd == 0.0, "a silent booking is the thing being banned"


def test_booking_a_healthy_account_does_nothing():
    """Creating an entry on books that already agree would make a correct
    account look repaired."""
    v = _venue()
    asyncio.run(_buy(v))
    assert v.absorb_gap("nothing wrong here") is None
    assert v.unexplained_usd == 0.0
    assert v.unexplained_reason is None


def test_a_second_gap_is_added_not_replaced():
    """Two unexplained events are two, and the account must say so."""
    v = _venue()
    asyncio.run(_buy(v, notional=40.0))
    v._positions.clear()
    v.absorb_gap("first")
    asyncio.run(_buy(v, notional=10.0, cid="b2"))
    v._positions.clear()
    v.absorb_gap("second")
    assert v.unexplained_usd == pytest.approx(-50.0, abs=0.01)
    assert "first" in v.unexplained_reason and "second" in v.unexplained_reason


def test_booking_a_gap_does_not_make_new_trades_reconcile_wrongly():
    """The booked amount is a constant offset, not a licence to drift."""
    v = _venue()
    asyncio.run(_buy(v, notional=43.75))
    v._positions.clear()
    v.absorb_gap("investigated")
    asyncio.run(_buy(v, notional=20.0, cid="b3"))
    assert v.reconcile() is None
    v._positions.clear()                        # a NEW unexplained loss
    assert v.reconcile() is not None, "a fresh gap must still be caught"


def test_the_state_restore_defers_to_the_venues_own_check():
    """`fund_state._check_cash` used to reimplement the identity. It drifted
    the moment the venue gained a suspense account: the venue reconciled, the
    duplicate did not, and startup warned about a gap that had already been
    booked and named."""
    from trading import fund_state

    v = _venue()
    asyncio.run(_buy(v, notional=43.75))
    v._positions.clear()
    v.absorb_gap("investigated")
    assert v.reconcile() is None
    assert fund_state._check_cash(v) == [], \
        "a booked gap must not be re-reported at every startup"

    v._positions.clear()
    asyncio.run(_buy(v, notional=10.0, cid="b9"))
    v._positions.clear()                      # a NEW gap
    assert fund_state._check_cash(v), "a fresh gap must still warn"


def test_a_venue_without_reconcile_still_gets_checked():
    """The fallback must survive: not every adapter is the paper venue."""
    from trading import fund_state

    class _Legacy:
        starting_cash_usd = 500.0
        cash_usd = 400.0
        realized_pnl_usd = 0.0
        _positions: dict = {}
    assert fund_state._check_cash(_Legacy())
