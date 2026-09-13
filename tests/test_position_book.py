"""Position lifecycle — stop enforcement and thesis resolution.

Before this existed the fund computed a stop, sized against it, graded against
it, and never looked at it again. These tests are the guard on that not
regressing.

- Acceptance: a breached stop produces an exit; a reached target produces an
  exit; closing records the thesis outcome.
- Blind: stop beats target when both are hit (never assume the good one);
  a missing quote is skipped, not treated as flat; averaging up must not
  loosen the original stop.
"""

from datetime import datetime

import pytest

from finance.exits import Bar, build_exit_plan
from memory.store import MemoryStore
from trading.fund import FundLoop, Holding
from trading.position_book import PositionBook
from trading.sessions import EASTERN


STEADY = [Bar(high=102, low=100, close=101) for _ in range(20)]
WEDNESDAY = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)


def _plan(entry=100.0, stop_mult=2.0, target_mult=4.0, direction="long"):
    return build_exit_plan(entry=entry, bars=STEADY, direction=direction,
                           stop_multiplier=stop_mult, target_multiplier=target_mult)


def _book(memory=None, **kw):
    b = PositionBook(memory=memory, **kw)
    b.open(symbol="AAPL", asset_class="equity", quantity=10,
           entry_price=100.0, plan=_plan(), thesis_id="t1")
    return b


# ---------------- the core guarantee ----------------

def test_breached_stop_produces_an_exit():
    book = _book()
    signals = book.check_exits({"AAPL": 95.0})     # stop is at 96
    assert len(signals) == 1
    assert signals[0].reason == "stop"


def test_reached_target_produces_an_exit():
    book = _book()
    signals = book.check_exits({"AAPL": 109.0})    # target is at 108
    assert signals[0].reason == "target"


def test_price_inside_the_plan_produces_nothing():
    assert _book().check_exits({"AAPL": 101.0}) == []


def test_stop_wins_when_both_are_hit():
    """A bar can straddle both; assuming the good one inflates every result."""
    book = PositionBook()
    book.open(symbol="X", asset_class="equity", quantity=1,
              entry_price=100.0, plan=_plan(stop_mult=0.5, target_mult=0.5))
    # Price below the stop; the target is above, but the stop is what counts.
    assert book.check_exits({"X": 50.0})[0].reason == "stop"


def test_missing_quote_is_skipped_not_assumed():
    """Acting on a stale price closes at a number that never existed."""
    assert _book().check_exits({}) == []


def test_short_position_stop_is_above_entry():
    book = PositionBook()
    book.open(symbol="S", asset_class="equity", quantity=1,
              entry_price=100.0, plan=_plan(direction="short"))
    assert book.check_exits({"S": 110.0})[0].reason == "stop"
    assert book.check_exits({"S": 90.0})[0].reason == "target"


# ---------------- closing + outcomes ----------------

def test_close_records_the_realized_return():
    book = _book()
    record = book.close("AAPL", 110.0, reason="target")
    assert record["realized_return"] == pytest.approx(0.10)
    assert record["realized_usd"] == pytest.approx(100.0)
    assert book.open_symbols() == []


def test_close_records_a_loss():
    book = _book()
    record = book.close("AAPL", 96.0, reason="stop")
    assert record["realized_return"] == pytest.approx(-0.04)


def test_closing_writes_the_thesis_outcome(tmp_path):
    """This is what makes the calibration scorecard non-empty."""
    store = MemoryStore(db_path=str(tmp_path / "p.db"))
    book = _book(memory=store)
    book.close("AAPL", 110.0, reason="target")

    outcome = store.get_thesis_outcome("t1")
    assert outcome is not None
    assert outcome["correct"] is True
    assert outcome["realized_return"] == pytest.approx(0.10)
    assert "target" in outcome["notes"]


def test_a_losing_thesis_is_recorded_as_incorrect(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "p.db"))
    book = _book(memory=store)
    book.close("AAPL", 96.0, reason="stop")
    assert store.get_thesis_outcome("t1")["correct"] is False


def test_close_without_a_thesis_id_records_nothing(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "p.db"))
    book = PositionBook(memory=store)
    book.open(symbol="X", asset_class="equity", quantity=1,
              entry_price=100.0, plan=_plan())
    book.close("X", 110.0)
    assert store.resolved_outcomes() == []


def test_outcome_failure_does_not_break_the_exit():
    class _Broken:
        def record_thesis_outcome(self, *a, **kw):
            raise RuntimeError("disk full")

    book = _book(memory=_Broken())
    assert book.close("AAPL", 110.0) is not None


def test_closing_an_unknown_symbol_is_none():
    assert PositionBook().close("NOPE", 1.0) is None


# ---------------- adding to a position ----------------

def test_averaging_up_keeps_the_original_stop():
    """A stop must not drift looser because we bought more."""
    book = _book()
    original_stop = book.get("AAPL").plan.stop
    book.open(symbol="AAPL", asset_class="equity", quantity=10,
              entry_price=120.0, plan=_plan(entry=120.0), thesis_id="t2")

    position = book.get("AAPL")
    assert position.quantity == 20
    assert position.entry_price == pytest.approx(110.0)
    assert position.plan.stop == original_stop


# ---------------- trailing ----------------

def test_trailing_ratchets_the_stop_when_enabled():
    book = _book(trailing=True)
    book.check_exits({"AAPL": 106.0})
    assert book.get("AAPL").plan.stop > 96.0


def test_trailing_is_off_by_default():
    book = _book()
    book.check_exits({"AAPL": 106.0})
    assert book.get("AAPL").plan.stop == pytest.approx(96.0)


# ---------------- reads ----------------

def test_unrealized_and_status():
    book = _book()
    assert book.unrealized_usd({"AAPL": 105.0}) == pytest.approx(50.0)

    s = book.status({"AAPL": 105.0})
    assert s["open"] == 1
    assert s["positions"][0]["unrealized_pct"] == pytest.approx(5.0)


def test_flatten_signals_filter_by_asset_class():
    book = PositionBook()
    book.open(symbol="BTC", asset_class="crypto", quantity=1,
              entry_price=100.0, plan=_plan())
    book.open(symbol="AAPL", asset_class="equity", quantity=1,
              entry_price=100.0, plan=_plan())

    signals = book.flatten_signals({"BTC": 101.0, "AAPL": 101.0},
                                   asset_class="crypto")
    assert [s.symbol for s in signals] == ["BTC"]


# ---------------- fund loop integration ----------------

class _Venue:
    def __init__(self):
        self.orders = []

    async def place(self, order, moment, **kw):
        from trading.venues.base import OrderAck
        self.orders.append(order)
        return OrderAck(accepted=True, client_order_id=order.client_order_id,
                        venue_order_id="v1", status="filled", venue="fake")


class _Data:
    def __init__(self, price):
        self.price = price

    async def get_quote(self, symbol):
        from trading.venues.base import Quote
        return Quote(symbol=symbol, bid=self.price - 0.01, ask=self.price + 0.01)

    async def get_history(self, symbol, lookback=60):
        return None

    async def get_financials(self, symbol):
        return None


@pytest.mark.asyncio
async def test_fund_cycle_exits_a_stopped_position(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "f.db"))
    book = _book(memory=store)
    router = _Venue()

    loop = FundLoop(router=router, pipeline=None, round_table=None,
                    data=_Data(95.0), position_book=book, memory=store)
    report = await loop.run_cycle(WEDNESDAY)

    assert len(report.exits) == 1
    assert "stop" in report.exits[0]["detail"]
    assert book.open_symbols() == []
    assert router.orders[0].side == "sell"
    # Sized in quantity, not notional.
    assert router.orders[0].quantity == 10
    assert router.orders[0].notional_usd is None


@pytest.mark.asyncio
async def test_a_failed_exit_is_reported_and_the_position_stays_open(tmp_path):
    """Silently dropping a failed stop is the worst possible outcome."""
    class _Refusing:
        async def place(self, order, moment, **kw):
            from trading.venues.base import OrderAck
            return OrderAck(accepted=False, client_order_id="c",
                            status="rejected", error="[session] market closed")

    book = _book()
    loop = FundLoop(router=_Refusing(), pipeline=None, round_table=None,
                    data=_Data(95.0), position_book=book)
    report = await loop.run_cycle(WEDNESDAY)

    assert report.exits == []
    assert any("EXIT FAILED" in e for e in report.errors)
    assert book.open_symbols() == ["AAPL"]      # retried next tick


@pytest.mark.asyncio
async def test_exits_run_even_when_the_kill_switch_tripped(tmp_path):
    """A tripped day must still let positions out."""
    from trading.kill_switch import DailyLossKillSwitch

    ks = DailyLossKillSwitch(max_daily_loss_usd=100.0)
    ks.observe_equity(WEDNESDAY, 10_000.0)
    ks.observe_equity(WEDNESDAY, 9_000.0)       # tripped

    book = _book()
    router = _Venue()
    loop = FundLoop(router=router, pipeline=None, round_table=None,
                    data=_Data(95.0), position_book=book, kill_switch=ks)
    report = await loop.run_cycle(WEDNESDAY, equity_usd=9_000.0)

    assert report.halted_reason is not None      # no NEW risk
    assert len(report.exits) == 1                # but the exit happened


@pytest.mark.asyncio
async def test_cycle_without_a_position_book_is_unaffected():
    loop = FundLoop(router=_Venue(), pipeline=None, round_table=None,
                    data=_Data(95.0))
    report = await loop.run_cycle(WEDNESDAY)
    assert report.exits == []
