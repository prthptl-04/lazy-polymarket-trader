"""A resting order that fills is a position. The book has to learn about it.

This is the cause of the $43.75 that vanished on 2026-09-22, found the
following hour when it happened again in front of me.

`fund_state._venue_rows` seeds the paper venue from the BOOK on restore —
deliberately, so the two can never come back disagreeing. The consequence
nobody had followed through: a position the BOOK does not know about is
destroyed on the next restart, while the cash that bought it is restored
verbatim. Cash down, inventory gone, no sale, no realised entry.

Only one path creates such a position, and it is the whole crypto strategy.
`FundLoop` books a fill when `result.filled`; a resting limit acks `open`, not
`filled`, so nothing was booked — and `built.exit_plan`, the plan the order was
sized against, went out of scope at the end of the cycle. When the order filled
hours later, `_reconcile_resting` recorded a note saying it "is held at the
venue" and that was the end of it.

Two failures, and the second is worse:

  1. The position is not persisted. That is the missing money.
  2. NOTHING WATCHES ITS STOP. `_process_exits` walks the book, so an unbooked
     position rides straight through the level its own thesis chose.

Caught live: PEPE-USD filled at 5.1671278e-06 for $29.17 and sat outside the
book, one restart away from vanishing exactly as its predecessor had.
"""

import asyncio
from datetime import datetime

import pytest
from zoneinfo import ZoneInfo

from finance.exits import ExitPlan
from trading.fund import CycleReport, FundLoop

ET = ZoneInfo("America/New_York")


class _Book:
    def __init__(self): self.opened = []
    def open(self, **kw):
        self.opened.append(kw)
        return kw


class _Ack:
    def __init__(self, cid="c1", symbol="PEPE-USD", qty=5_644_663.95,
                 price=5.1671278e-06):
        self.client_order_id = cid
        self.venue = "paper"
        self.raw = {"symbol": symbol, "quantity": qty, "fill_price": price}


def _fund(book=None, plans=None):
    f = object.__new__(FundLoop)
    f.position_book = book
    f._pending_plans = dict(plans or {})
    return f


def _report():
    return CycleReport(moment=datetime(2026, 9, 22, 4, 45, tzinfo=ET),
                       session="premarket")


def _plan(symbol="PEPE-USD"):
    return {
        "symbol": symbol, "asset_class": "crypto",
        "plan": {"entry": 5.2e-06, "stop": 4.9e-06, "target": 5.8e-06,
                 "direction": "long", "atr": 2.0e-07},
        "planned_entry": 5.2e-06, "spread_bps_at_entry": 190,
        "thesis_id": "t-1", "signal": "bullish", "confidence": 55.0,
        "venue": "paper", "mode": "paper",
    }


# ---------- the fill reaches the book ----------

def test_a_resting_fill_is_booked_with_the_plan_it_was_sized_on():
    book = _Book()
    fund = _fund(book, {"c1": _plan()})
    report = _report()
    FundLoop._book_resting_fill(fund, _Ack(), report)

    assert len(book.opened) == 1, "the position must reach the book"
    kw = book.opened[0]
    assert kw["symbol"] == "PEPE-USD"
    assert kw["quantity"] == pytest.approx(5_644_663.95)
    assert kw["entry_price"] == pytest.approx(5.1671278e-06)
    assert isinstance(kw["plan"], ExitPlan)
    assert kw["plan"].stop == pytest.approx(4.9e-06), "the stop is watched"
    assert kw["thesis_id"] == "t-1"
    assert report.errors == []


def test_the_entry_price_is_the_fill_not_the_plan():
    """The plan was drawn on a mid. The book must carry what was paid."""
    book = _Book()
    fund = _fund(book, {"c1": _plan()})
    FundLoop._book_resting_fill(fund, _Ack(price=5.0e-06), _report())
    assert book.opened[0]["entry_price"] == pytest.approx(5.0e-06)
    assert book.opened[0]["planned_entry"] == pytest.approx(5.2e-06)
    assert book.opened[0]["entry_fill_source"] == "venue"


def test_a_plan_is_consumed_so_one_order_books_once():
    book = _Book()
    fund = _fund(book, {"c1": _plan()})
    FundLoop._book_resting_fill(fund, _Ack(), _report())
    report = _report()
    FundLoop._book_resting_fill(fund, _Ack(), report)
    assert len(book.opened) == 1, "a second fill on one order is not a position"
    assert report.errors, "and the second is reported, not swallowed"


# ---------- a missing plan is loud, never invented ----------

def test_a_fill_with_no_stored_plan_is_a_real_error():
    """A fabricated stop is worse than a missing one: it looks like a decision
    somebody made."""
    book = _Book()
    fund = _fund(book, {})
    report = _report()
    FundLoop._book_resting_fill(fund, _Ack(), report)
    assert book.opened == [], "no invented stop"
    assert len(report.errors) == 1
    assert "PEPE-USD" in report.errors[0]
    assert "stop is unwatched" in report.errors[0]
    assert report.notes == [], "this is a fault, not a note"


def test_a_malformed_ack_is_reported_not_booked():
    book = _Book()
    fund = _fund(book, {"c1": _plan()})
    ack = _Ack()
    ack.raw = {"symbol": "PEPE-USD"}          # no quantity, no price
    report = _report()
    FundLoop._book_resting_fill(fund, ack, report)
    assert book.opened == []
    assert report.errors


def test_a_book_that_raises_does_not_lose_the_cycle():
    class _Boom:
        def open(self, **kw): raise RuntimeError("book is wedged")
    fund = _fund(_Boom(), {"c1": _plan()})
    report = _report()
    FundLoop._book_resting_fill(fund, _Ack(), report)      # must not raise
    assert report.errors


def test_no_book_is_a_no_op():
    fund = _fund(None, {"c1": _plan()})
    report = _report()
    FundLoop._book_resting_fill(fund, _Ack(), report)
    assert report.errors == []


# ---------- the plan survives the process ----------

def test_a_pending_plan_is_snapshotted_and_restored():
    """An order outlives the process, so its plan has to as well."""
    from trading import fund_state

    class _B:
        positions = {}
        def snapshot(self): return []

    state = fund_state.snapshot(book=_B(), venue=None,
                                pending_plans={"c1": _plan()})
    assert state["pending_plans"]["c1"]["thesis_id"] == "t-1"

    class _Mem:
        def get(self, agent, key, default=None): return state
    fund = _fund(_Book())
    fund_state.restore_pending_plans(_Mem(), fund)
    assert fund._pending_plans["c1"]["thesis_id"] == "t-1"


def test_an_older_blob_with_no_plans_restores_empty_not_broken():
    """No version bump: a blob written before plans were stored is readable,
    and simply carries none. Its fills report the miss loudly."""
    from trading import fund_state

    class _Mem:
        def get(self, agent, key, default=None):
            return {"version": 1, "positions": []}
    fund = _fund(_Book())
    fund_state.restore_pending_plans(_Mem(), fund)
    assert fund._pending_plans == {}


def test_unreadable_memory_leaves_the_fund_startable():
    from trading import fund_state

    class _Mem:
        def get(self, *a, **k): raise RuntimeError("db gone")
    fund = _fund(_Book())
    fund_state.restore_pending_plans(_Mem(), fund)
    assert fund._pending_plans == {}


def test_the_wiring_stores_a_plan_when_an_entry_rests():
    """The other half: a resting entry must PUT its plan somewhere."""
    import inspect

    src = inspect.getsource(FundLoop._deliberate_one) \
        if hasattr(FundLoop, "_deliberate_one") else inspect.getsource(FundLoop)
    assert "_pending_plans[" in src
    assert "not result.filled" in src


def test_the_snapshot_closure_reads_the_live_fund():
    """A snapshot that captured an empty dict at build time would persist
    nothing, which is the same bug with extra steps."""
    import inspect

    from dashboard import fund_wiring
    src = inspect.getsource(fund_wiring.build_fund)
    assert 'fund_cell["loop"] = fund' in src
    assert "pending_plans=getattr(loop" in src
