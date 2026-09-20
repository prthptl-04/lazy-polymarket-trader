"""Venue engines, and the per-seat examination matrix.

- Blind: an engine must refuse to start while the fund is stopped, and must
  refuse an unauthenticated venue. A button that appears to work and connects
  to nothing is worse than a disabled one.
- Blind: the matrix must never blame a dissenter, and must never print a
  recommendation as though the fund were already enforcing it.
"""

import pytest

from dashboard.runtime import DashboardRuntime
from memory.store import MemoryStore
from roundtable.postmortem import Postmortem
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter


class _Session:
    def __init__(self, authed): self._authed = authed
    def auth_summary(self): return {"authenticated": self._authed}


class _Live:
    def __init__(self, authed=True):
        self.session = _Session(authed)
    name = "robinhood"
    is_live = True
    def supports(self, ac): return ac in ("equity", "crypto")


def _runtime(tmp_path, adapters, *, running: bool):
    class Fund: pass
    class Sched: pass
    fund, sched = Fund(), Sched()
    fund.router = VenueRouter(adapters=adapters)
    sched.fund = fund
    sched.state = "running" if running else "stopped"
    return DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "e.db")),
                            fund_scheduler=sched)


def test_engine_refuses_to_start_while_the_system_is_stopped(tmp_path):
    rt = _runtime(tmp_path, [_Live()], running=False)
    result = rt.set_engine("robinhood", True)
    assert result["ok"] is False and "stopped" in result["reason"]


def test_engine_refuses_an_unauthenticated_venue(tmp_path):
    rt = _runtime(tmp_path, [_Live(authed=False)], running=True)
    result = rt.set_engine("robinhood", True)
    assert result["ok"] is False and "not authenticated" in result["reason"]


def test_engine_starts_when_the_system_runs_and_the_venue_is_authenticated(tmp_path):
    rt = _runtime(tmp_path, [_Live()], running=True)
    assert rt.set_engine("robinhood", True)["ok"] is True
    assert rt.engines()["robinhood"]["on"] is True


def test_stopping_an_engine_never_needs_the_system_running(tmp_path):
    """You must always be able to cut a venue off."""
    rt = _runtime(tmp_path, [_Live()], running=False)
    assert rt.set_engine("robinhood", False)["ok"] is True


def test_a_retired_venue_says_it_is_retired_not_that_it_is_misconfigured(tmp_path):
    """Polymarket has no adapter because it was retired, not because someone
    forgot to wire one. "No adapter registered" reads as a fixable gap and
    would have an operator hunting for the configuration mistake."""
    state = _runtime(tmp_path, [_Live()], running=True).engines()["polymarket_us"]
    assert state["attached"] is False
    assert state["retired"] is True
    assert state["on"] is False
    assert "Retired" in state["reason"] and "Robinhood only" in state["reason"]


def test_a_retired_venue_cannot_be_started(tmp_path):
    """The engine button must not be able to revive a retired venue."""
    rt = _runtime(tmp_path, [_Live()], running=True)
    result = rt.set_engine("polymarket_us", True)
    assert result["ok"] is False and "Retired" in result["reason"]
    assert rt.engines()["polymarket_us"]["on"] is False


def test_robinhood_is_not_retired(tmp_path):
    """The one venue the fund actually trades."""
    assert _runtime(tmp_path, [_Live()], running=True).engines()["robinhood"]["retired"] is False


def test_paper_adapter_is_not_described_as_authenticated(tmp_path):
    """It has no credentials; claiming authentication would overstate it."""
    state = _runtime(tmp_path, [PaperVenue()], running=True).engines()["robinhood"]
    assert state["adapter"] == "paper"
    assert "nothing to authenticate" in state["reason"]


# ---------- matrix ----------

def _seeded(tmp_path):
    st = MemoryStore(db_path=str(tmp_path / "m.db"))
    st.save_deliberation("t1", "AAPL", "equity", "complete", {
        "opinions": [
            {"seat_id": "quant", "seat_name": "Quantitative Analyst", "signal": "bullish",
             "confidence": 90, "reasoning": "momentum", "failed": False},
            {"seat_id": "risk", "seat_name": "Risk Manager", "signal": "bearish",
             "confidence": 65, "reasoning": "stop in noise", "failed": False},
        ],
        "consensus": {"signal": "bullish", "confidence": 85},
        "tally": {"bullish": 1, "bearish": 1},
    }, signal="bullish", confidence=85.0)
    st.record_thesis_outcome("t1", "AAPL", realized_return=-0.06, signal="bullish",
                             confidence=85.0, correct=False)
    Postmortem(memory=st).run(symbol="AAPL", realized_return=-0.06,
                              thesis=st.get_deliberation("t1"))
    return DashboardRuntime(memory=st)


def test_per_seat_blame_is_withheld_below_the_sample_bar(tmp_path):
    """The fund is long-only and `correct` is `realized > 0`, so every bullish
    seat is scored on the identical event. "The Quant has backed 2 of 2 losses"
    is a rate at n=2, and it is injected nowhere near a decision until 30."""
    rows = {r["id"]: r for r in _seeded(tmp_path).agent_matrix()}
    assert rows["quant"]["blamed_losses"] == 0
    assert rows["risk"]["blamed_losses"] == 0


def test_the_per_trade_attribution_survives_the_gate(tmp_path):
    """A transcript of ONE displayed trade is not a rate across trades: on this
    loss, these seats backed it and this one objected. That is how a human reads
    a post-mortem, and it stays."""
    row = _seeded(tmp_path).trade_history()[0]
    assert [b["name"] for b in row["blamed"]] == ["Quantitative Analyst"]
    assert [v["name"] for v in row["vindicated"]] == ["Risk Manager"]


def test_blame_rates_appear_once_the_sample_supports_them(tmp_path):
    from roundtable.calibration import MIN_SAMPLES_FOR_SEAT_SCORE
    st = MemoryStore(db_path=str(tmp_path / "many.db"))
    for i in range(MIN_SAMPLES_FOR_SEAT_SCORE):
        st.save_deliberation(f"t{i}", "AAPL", "equity", "complete", {
            "opinions": [
                {"seat_id": "quant", "seat_name": "Quantitative Analyst",
                 "signal": "bullish", "confidence": 90, "reasoning": "m", "failed": False},
                {"seat_id": "risk", "seat_name": "Risk Manager",
                 "signal": "bearish", "confidence": 65, "reasoning": "s", "failed": False},
            ],
            "consensus": {"signal": "bullish", "confidence": 85},
            "tally": {"bullish": 1, "bearish": 1},
        }, signal="bullish", confidence=85.0)
        st.record_thesis_outcome(f"t{i}", "AAPL", realized_return=-0.05,
                                 signal="bullish", confidence=85.0, correct=False)
    rows = {r["id"]: r for r in DashboardRuntime(memory=st).agent_matrix()}
    assert rows["quant"]["blamed_losses"] > 0
    assert rows["risk"]["blamed_losses"] == 0, "a dissenter is never blamed"


def test_overconfidence_is_flagged_not_reported_as_enforced(tmp_path):
    """The fund applies a global shrink; a per-seat tightening is advice."""
    quant = {r["id"]: r for r in _seeded(tmp_path).agent_matrix()}["quant"]
    assert any("scaled by" in a for a in quant["enforced"]["applied"])
    assert any("overstates" in f for f in quant["enforced"]["flagged"])


def test_every_seat_appears_even_unscored(tmp_path):
    """A seat missing from the panel reads as a seat that does not exist."""
    rows = _seeded(tmp_path).agent_matrix()
    assert len(rows) == 7
    unscored = [r for r in rows if r["samples"] == 0]
    assert unscored and all(r["hit_rate"] is None for r in unscored)
    assert all("unscored" in r["target"]["note"] for r in unscored)


# ---------- per-venue record ----------

def _book_with(closed):
    class Book:
        def __init__(self): self.closed = closed
    return Book()


def test_record_splits_by_venue(tmp_path):
    rt = DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "r.db")),
                          position_book=_book_with([
                              {"symbol": "AAPL", "asset_class": "equity", "realized_usd": 40.0},
                              {"symbol": "BTC", "asset_class": "crypto", "realized_usd": -10.0},
                              {"symbol": "will-x", "asset_class": "prediction", "realized_usd": 25.0},
                          ]))
    assert rt.record()["closed"] == 3
    assert rt.record("robinhood")["closed"] == 2
    assert rt.record("robinhood")["realized_usd"] == 30.0
    assert rt.record("polymarket_us")["closed"] == 1
    assert rt.record("polymarket_us")["realized_usd"] == 25.0


def test_trades_closed_before_asset_class_was_recorded_are_fund_wide_only(tmp_path):
    """Filing an unattributable trade under the broker would invent a history."""
    rt = DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "r2.db")),
                          position_book=_book_with([{"symbol": "OLD", "realized_usd": 12.0}]))
    assert rt.record()["closed"] == 1
    assert rt.record("robinhood")["closed"] == 0
    assert rt.record("polymarket_us")["closed"] == 0


# ---------- venue stats: fund record vs broker record ----------

class _WithLedger(_Live):
    def __init__(self, stats): super().__init__(); self._stats = stats
    async def realized_stats(self): return self._stats
    async def account(self):
        from trading.venues.base import AccountSnapshot
        return AccountSnapshot(equity_usd=500.0, buying_power_usd=500.0,
                               cash_usd=500.0, venue="robinhood")


@pytest.mark.asyncio
async def test_broker_headlines_when_it_is_reachable(tmp_path):
    rt = _runtime(tmp_path, [_WithLedger({"source": "broker", "available": True,
                                          "realized_usd": 12.0})], running=True)
    out = await rt.venue_stats("robinhood")
    assert out["primary"] == "broker"
    assert out["broker"]["equity_usd"] == 500.0
    # The fund's own record is still carried, never merged into the broker's.
    assert out["fund"]["closed"] == 0


@pytest.mark.asyncio
async def test_unreachable_broker_falls_back_to_the_fund_record(tmp_path):
    rt = _runtime(tmp_path, [_WithLedger({"source": "broker", "available": False,
                                          "reason": "not authenticated"})], running=True)
    out = await rt.venue_stats("robinhood")
    assert out["primary"] == "fund"
    assert out["broker"]["reason"] == "not authenticated"


@pytest.mark.asyncio
async def test_a_venue_without_a_ledger_reports_no_broker(tmp_path):
    out = await _runtime(tmp_path, [PaperVenue()], running=True).venue_stats("paper")
    assert out["broker"] is None and out["primary"] == "fund"


# ---------- the strategy split ----------
#
# One venue now, so the interesting cut is no longer venue vs venue but the two
# strategies inside Robinhood: equities Monday to Friday, crypto at weekends.
# They run on different calendars and a blended number hides which is working.

def _closed(store, symbol, asset_class, pnl):
    import time
    now = time.time()
    store.record_closed_trade({
        "symbol": symbol, "asset_class": asset_class, "realized_usd": pnl,
        "realized_return": pnl / 1000.0, "quantity": 1, "entry_price": 1.0,
        "exit_price": 2.0, "reason": "test", "venue": "robinhood",
        "mode": "paper", "opened_at": now - 600, "closed_at": now,
        "held_seconds": 600,
    })


def _with_trades(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "split.db"))
    _closed(store, "AAPL", "equity", 120.0)
    _closed(store, "MSFT", "equity", -40.0)
    _closed(store, "BTC-USD", "crypto", 300.0)
    _closed(store, "ETH-USD", "crypto", -50.0)
    return DashboardRuntime(memory=store)


def test_asset_class_slices_the_record(tmp_path):
    rt = _with_trades(tmp_path)
    assert rt.record("robinhood")["closed"] == 4
    assert rt.record("robinhood", asset_class="equity")["closed"] == 2
    assert rt.record("robinhood", asset_class="crypto")["closed"] == 2


def test_the_split_shows_which_strategy_is_carrying_the_book(tmp_path):
    """The whole point of splitting. Blended it is +330 and looks uniform;
    split it is +80 equities against +250 crypto, which is a different fund."""
    rt = _with_trades(tmp_path)
    assert rt.record("robinhood")["realized_usd"] == pytest.approx(330.0)
    assert rt.record("robinhood", asset_class="equity")["realized_usd"] == pytest.approx(80.0)
    assert rt.record("robinhood", asset_class="crypto")["realized_usd"] == pytest.approx(250.0)


def test_the_slices_sum_to_the_whole(tmp_path):
    """A split that does not reconcile is worse than no split."""
    rt = _with_trades(tmp_path)
    parts = sum(rt.record("robinhood", asset_class=a)["closed"] for a in ("equity", "crypto"))
    assert parts == rt.record("robinhood")["closed"]


@pytest.mark.asyncio
async def test_a_sliced_panel_suppresses_the_broker_ledger(tmp_path):
    """The broker's ledger is account-wide and cannot be cut by asset class.

    Showing it under an "Equities" heading would read as the equity strategy's
    P&L and be wrong by every crypto trade in the account.
    """
    rt = _with_trades(tmp_path)
    assert (await rt.venue_stats("robinhood", asset_class="equity"))["broker"] is None
    assert (await rt.venue_stats("robinhood", asset_class="equity"))["primary"] == "fund"


@pytest.mark.asyncio
async def test_an_unsliced_panel_still_reports_its_asset_class_as_none(tmp_path):
    stats = await _with_trades(tmp_path).venue_stats("robinhood")
    assert stats["asset_class"] is None
