"""The fund loop — one end-to-end cycle.

This is the integration test for everything since the pivot: data → screens →
pre-screen veto → round table → sizing → grader → router → venue.

- Acceptance: a bullish cycle on a weekday opens a position.
- Blind (the orderings that matter): equity is observed BEFORE anything trades;
  the pre-screen vetoes BEFORE any LLM call is made; a tripped kill-switch
  halts the cycle; the session decides the universe.
- Edge: missing data, one bad symbol not killing the cycle, weekend flatten.
"""

import json
from datetime import datetime, timedelta

import pytest

from finance.exits import Bar
from finance.quality import Financials
from memory.store import MemoryStore
from roundtable.engine import RoundTable
from trading.fund import FundLoop, Holding
from trading.kill_switch import DailyLossKillSwitch
from trading.market_data import StaticProvider
from trading.pipeline import ThesisPipeline
from trading.sessions import EASTERN
from trading.venues.base import Quote
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter
from verification.criteria import VerifiedOutcomeCriteria
from verification.outcome_grader import OutcomeGrader


WEDNESDAY = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)
SATURDAY = datetime(2026, 9, 19, 12, 0, tzinfo=EASTERN)
SUNDAY_NIGHT = datetime(2026, 9, 21, 3, 30, tzinfo=EASTERN)   # inside flatten window

CRITERIA = VerifiedOutcomeCriteria(max_position_usd=2_000.0, min_expected_edge_bps=20)

RISING_BARS = [Bar(high=100 + i, low=98 + i, close=99 + i) for i in range(30)]
HEALTHY = Financials(total_assets=1000, total_liabilities=300,
                     current_assets=500, current_liabilities=200,
                     retained_earnings=400, ebit=150, revenue=1200, market_cap=2000)
DISTRESSED = Financials(total_assets=1000, total_liabilities=1200,
                        current_assets=100, current_liabilities=400,
                        retained_earnings=-500, ebit=-100, revenue=200, market_cap=50)


# ---------------- fakes ----------------

class _Block:
    def __init__(self, text):
        self.text = text
        self.type = "text"


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _Client:
    """Every seat votes bullish; the chair resolves bullish."""

    def __init__(self, signal="bullish", confidence=85):
        self.calls = 0
        outer = self

        class _M:
            def create(self, **kw):
                outer.calls += 1
                system = kw["system"]
                text = system[0]["text"] if isinstance(system, list) else str(system)
                if "Chair of an investment committee" in text:
                    return _Resp(json.dumps({
                        "signal": signal, "confidence": confidence,
                        "summary": "s", "dissent": "d", "transcript": "[Chair]: ok",
                    }))
                return _Resp(json.dumps({
                    "signal": signal, "confidence": confidence,
                    "reasoning": "r", "key_points": [], "concerns": [],
                }))

        self.messages = _M()


def _build(watchlist=("AAPL",), crypto=(), financials=HEALTHY,
           bankroll=10_000.0, signal="bullish", kill_switch=None, memory=None):
    venue = PaperVenue(starting_cash_usd=bankroll, slippage_bps=0,
                       supported=("equity", "crypto"))
    for sym in list(watchlist) + list(crypto):
        venue.set_quote(sym, bid=99.95, ask=100.05)   # 10 bps — a liquid name

    router = VenueRouter(adapters=[venue], kill_switch=kill_switch)
    pipeline = ThesisPipeline(
        router=router, grader=OutcomeGrader(CRITERIA), criteria=CRITERIA,
        bankroll_usd=bankroll,
    )
    client = _Client(signal=signal)
    table = RoundTable(client=client, memory=memory)

    data = StaticProvider()
    for sym in list(watchlist) + list(crypto):
        data.set_history(sym, RISING_BARS)
        data.quotes[sym] = Quote(symbol=sym, bid=99.95, ask=100.05)
        if financials is not None and sym in watchlist:
            data.financials[sym] = (financials, financials)

    loop = FundLoop(
        router=router, pipeline=pipeline, round_table=table, data=data,
        equity_watchlist=watchlist, crypto_watchlist=crypto,
        kill_switch=kill_switch, memory=memory,
    )
    return loop, venue, client


# ---------------- end to end ----------------

@pytest.mark.asyncio
async def test_bullish_weekday_cycle_opens_a_position():
    loop, venue, _ = _build()
    report = await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)

    assert report.session == "regular"
    assert report.deliberated == ["AAPL"]
    assert len(report.submitted) == 1
    assert len(await venue.positions()) == 1


@pytest.mark.asyncio
async def test_cycle_summary_shape():
    loop, _, _ = _build()
    s = (await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)).summary()
    for key in ("session", "universe", "prescreened_out", "deliberated",
                "submitted", "flattened", "errors"):
        assert key in s


@pytest.mark.asyncio
async def test_bearish_cycle_opens_nothing():
    loop, venue, _ = _build(signal="bearish")
    report = await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)
    assert report.submitted == []
    assert await venue.positions() == []


# ---------------- the pre-screen saves LLM spend ----------------

@pytest.mark.asyncio
async def test_distressed_company_never_reaches_the_round_table():
    """Microseconds of arithmetic must gate dollars of tokens."""
    loop, _, client = _build(financials=DISTRESSED)
    report = await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)

    assert report.deliberated == []
    assert client.calls == 0                       # not one LLM call
    assert report.prescreened_out[0]["rejected_by"] == "altman_distress"


@pytest.mark.asyncio
async def test_unbuildable_exit_plan_skips_the_table():
    loop, _, client = _build()
    loop.data.set_history("AAPL", [Bar(high=1, low=1, close=1) for _ in range(10)])
    report = await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)

    assert client.calls == 0
    assert report.prescreened_out[0]["rejected_by"] == "exit_plan"


@pytest.mark.asyncio
async def test_missing_price_data_is_skipped_not_fatal():
    loop, _, client = _build(watchlist=("AAPL", "GHOST"))
    loop.data.histories.pop("GHOST")
    loop.data.quotes.pop("GHOST")

    report = await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)
    assert "GHOST" in [p["symbol"] for p in report.prescreened_out]
    assert "AAPL" in report.deliberated


# ---------------- the session decides the universe ----------------

@pytest.mark.asyncio
async def test_weekend_uses_the_crypto_universe():
    loop, _, _ = _build(watchlist=("AAPL",), crypto=("BTC",), financials=None)
    report = await loop.run_cycle(SATURDAY, equity_usd=10_000.0)

    assert report.session == "crypto_only"
    assert report.universe == ["BTC"]
    assert "AAPL" not in report.deliberated


@pytest.mark.asyncio
async def test_weekday_uses_the_equity_universe():
    loop, _, _ = _build(watchlist=("AAPL",), crypto=("BTC",))
    report = await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)
    assert report.universe == ["AAPL"]


@pytest.mark.asyncio
async def test_empty_universe_ends_the_cycle_quietly():
    loop, _, client = _build(watchlist=(), crypto=())
    report = await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)
    assert report.universe == []
    assert client.calls == 0


# ---------------- kill-switch ordering ----------------

@pytest.mark.asyncio
async def test_equity_is_observed_before_anything_trades():
    """A cycle that traded before observing would run with the switch unarmed."""
    ks = DailyLossKillSwitch(max_daily_loss_usd=100.0)
    loop, _, _ = _build(kill_switch=ks)

    await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)
    assert ks.is_armed(WEDNESDAY)


@pytest.mark.asyncio
async def test_tripped_switch_halts_the_cycle_before_any_llm_call():
    ks = DailyLossKillSwitch(max_daily_loss_usd=100.0)
    ks.observe_equity(WEDNESDAY, 10_000.0)
    loop, venue, client = _build(kill_switch=ks)

    report = await loop.run_cycle(WEDNESDAY, equity_usd=9_500.0)   # -500, trips

    assert report.halted_reason is not None
    assert "DAILY LOSS LIMIT" in report.halted_reason
    assert report.deliberated == []
    assert client.calls == 0
    assert await venue.positions() == []


@pytest.mark.asyncio
async def test_healthy_day_does_not_halt():
    ks = DailyLossKillSwitch(max_daily_loss_usd=1_000.0)
    loop, _, _ = _build(kill_switch=ks)
    report = await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)
    assert report.halted_reason is None


# ---------------- weekend → weekday handoff ----------------

@pytest.mark.asyncio
async def test_crypto_is_flattened_before_the_equity_open():
    loop, venue, _ = _build(watchlist=("AAPL",), crypto=("BTC",), financials=None)
    # Establish a weekend crypto position.
    await venue.place_order(__import__(
        "trading.venues.base", fromlist=["OrderRequest"]
    ).OrderRequest(symbol="BTC", side="buy", asset_class="crypto", quantity=2))

    report = await loop.run_cycle(
        SUNDAY_NIGHT, equity_usd=10_000.0,
        holdings=[Holding("BTC", "crypto", 2.0)],
    )
    assert "BTC" in report.flattened


@pytest.mark.asyncio
async def test_equities_are_not_flattened_by_the_crypto_handoff():
    loop, venue, _ = _build(watchlist=("AAPL",), crypto=("BTC",), financials=None)
    report = await loop.run_cycle(
        SUNDAY_NIGHT, equity_usd=10_000.0,
        holdings=[Holding("AAPL", "equity", 5.0)],
    )
    assert report.flattened == []


@pytest.mark.asyncio
async def test_no_flatten_during_the_weekend_itself():
    loop, _, _ = _build(crypto=("BTC",), financials=None)
    report = await loop.run_cycle(
        SATURDAY, equity_usd=10_000.0,
        holdings=[Holding("BTC", "crypto", 2.0)],
    )
    assert report.flattened == []


# ---------------- resilience ----------------

@pytest.mark.asyncio
async def test_one_bad_symbol_does_not_end_the_cycle():
    loop, _, _ = _build(watchlist=("BOOM", "AAPL"))

    original = loop.data.get_history

    async def flaky(symbol, *, lookback=60):
        if symbol == "BOOM":
            raise RuntimeError("feed exploded")
        return await original(symbol, lookback=lookback)

    loop.data.get_history = flaky
    report = await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)

    assert any("BOOM" in e for e in report.errors)
    assert "AAPL" in report.deliberated


@pytest.mark.asyncio
async def test_candidate_cap_bounds_llm_spend():
    loop, _, _ = _build(watchlist=("A", "B", "C", "D", "E", "F", "G"))
    loop.max_candidates_per_cycle = 2
    report = await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)
    assert len(report.deliberated) == 2


# ---------------- portfolio context reaches the seats ----------------

@pytest.mark.asyncio
async def test_seats_are_told_the_pdt_and_loss_headroom():
    from trading.pdt import DayTradeTracker

    ks = DailyLossKillSwitch(max_daily_loss_usd=500.0)
    loop, _, client = _build(kill_switch=ks)
    loop.router.pdt = DayTradeTracker(account_equity_usd=5_000.0)

    captured = []
    original = loop.round_table.deliberate

    async def spy(candidate):
        captured.append(candidate.evidence_block())
        return await original(candidate)

    loop.round_table.deliberate = spy
    await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)

    block = captured[0]
    assert "Day-trade budget" in block
    assert "Daily loss headroom" in block


# ---------------- resume ----------------

@pytest.mark.asyncio
async def test_resume_surfaces_unfinished_theses(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "fund.db"))
    store.save_deliberation("t-abandoned", "AAPL", "equity", "in_progress", {})

    loop, _, _ = _build(memory=store)
    assert await loop.resume_unfinished() == ["t-abandoned"]


@pytest.mark.asyncio
async def test_resume_is_empty_without_memory():
    loop, _, _ = _build()
    assert await loop.resume_unfinished() == []


@pytest.mark.asyncio
async def test_cycle_is_audited(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "fund.db"))
    loop, _, _ = _build(memory=store)
    await loop.run_cycle(WEDNESDAY, equity_usd=10_000.0)

    actions = [e["action"] for e in store.recent_audit_events(limit=20)]
    assert "cycle_complete" in actions
