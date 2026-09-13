"""The fund writes the ledger rule #13 counts.

Nothing in the fund path wrote `trade_log` — its only caller was the removed
CLOB Executor — so `LiveTradingGate._graded_count()` and the dashboard's paper
progress bar both read an empty table and returned 0 forever. The gate was
unreachable by construction.

- Blind: a graded-and-REJECTED trade is still written. It is evidence about the
  grader, and the gate filters on grade_pass itself.
- Blind: `paper` comes from the venue that took the order, never from the
  environment. Counting an unattributable fill as paper would pad the very bar
  that gates live trading.
"""

from datetime import datetime

import pytest

from finance.exits import ExitPlan
from memory.store import MemoryStore
from roundtable.types import Candidate, Thesis, Consensus, SeatOpinion
from trading.pipeline import ThesisPipeline
from trading.sessions import EASTERN
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter
from verification.criteria import VerifiedOutcomeCriteria
from verification.outcome_grader import OutcomeGrader

MOMENT = datetime(2026, 9, 14, 11, 0, tzinfo=EASTERN)


def _candidate(price: float = 100.0) -> Candidate:
    return Candidate(symbol="AAPL", asset_class="equity", price=price,
                     session="regular", spread_bps=4, atr=2.0,
                     entry=price, stop=price - 4.0, target=price + 8.0)


def _plan(price: float = 100.0) -> ExitPlan:
    return ExitPlan(entry=price, stop=price - 4.0, target=price + 8.0,
                    direction="long", atr=2.0)


def _thesis(confidence: float = 80.0) -> Thesis:
    """Three responding seats — the pipeline requires a quorum before it sizes."""
    t = Thesis(symbol="AAPL", asset_class="equity")
    for sid, name in (("quant", "Quantitative Analyst"), ("analyst", "Fundamental Analyst"),
                      ("risk", "Risk Manager")):
        t.opinions.append(SeatOpinion(seat_id=sid, seat_name=name, signal="bullish",
                                      confidence=confidence, reasoning="momentum"))
    t.consensus = Consensus(signal="bullish", confidence=confidence,
                            summary="constructive", transcript="")
    return t


def _pipeline(tmp_path, venue=None):
    memory = MemoryStore(db_path=str(tmp_path / "led.db"))
    v = venue or PaperVenue()
    if isinstance(v, PaperVenue):
        v.set_quote("AAPL", bid=99.98, ask=100.02)
    router = VenueRouter(adapters=[v])
    return ThesisPipeline(router=router, grader=OutcomeGrader(VerifiedOutcomeCriteria()),
                          criteria=VerifiedOutcomeCriteria(), bankroll_usd=1000.0,
                          memory=memory), memory


@pytest.mark.asyncio
async def test_a_submitted_trade_is_written_to_the_ledger(tmp_path):
    pipeline, memory = _pipeline(tmp_path)
    await pipeline.run(_thesis(), _candidate(), _plan(), MOMENT)
    rows = memory.recent_trades(limit=10)
    assert len(rows) == 1
    assert rows[0]["market_id"] == "AAPL" and rows[0]["side"] == "buy"
    assert rows[0]["paper"] == 1
    assert rows[0]["grade_pass"] == 1


@pytest.mark.asyncio
async def test_a_graded_rejection_is_still_written(tmp_path):
    """Evidence about the grader. The gate filters on grade_pass itself."""
    strict = VerifiedOutcomeCriteria(min_reward_risk_ratio=99.0)
    memory = MemoryStore(db_path=str(tmp_path / "rej.db"))
    venue = PaperVenue(); venue.set_quote("AAPL", bid=99.98, ask=100.02)
    pipeline = ThesisPipeline(router=VenueRouter(adapters=[venue]),
                              grader=OutcomeGrader(strict), criteria=strict,
                              bankroll_usd=1000.0, memory=memory)
    result = await pipeline.run(_thesis(), _candidate(), _plan(), MOMENT)
    assert result.outcome == "rejected"
    rows = memory.recent_trades(limit=10)
    assert len(rows) == 1 and rows[0]["grade_pass"] == 0


@pytest.mark.asyncio
async def test_a_live_venue_is_never_logged_as_paper(tmp_path):
    """A config drift must not pad the bar that gates live trading."""
    # PaperVenue is a dataclass: subclassing and assigning class attributes
    # does NOT change the generated __init__, which would quietly rebuild the
    # instance as paper. Declare liveness through the constructor.
    venue = PaperVenue(name="robinhood", is_live=True)
    pipeline, memory = _pipeline(tmp_path, venue=venue)
    venue.set_quote("AAPL", bid=99.98, ask=100.02)
    await pipeline.run(_thesis(), _candidate(), _plan(), MOMENT)
    rows = memory.recent_trades(limit=10)
    assert rows and rows[0]["paper"] == 0


@pytest.mark.asyncio
async def test_orders_are_logged_with_whether_they_actually_traded(tmp_path):
    pipeline, memory = _pipeline(tmp_path)
    await pipeline.run(_thesis(), _candidate(), _plan(), MOMENT)
    row = memory.recent_trades(limit=1)[0]
    assert row["filled"] == 1 and row["session"] == "regular" and row["venue"] == "paper"


@pytest.mark.asyncio
async def test_graded_orders_alone_do_not_open_the_live_bar(tmp_path):
    """The gate counts closed round trips. Orders that never traded — which is
    every extended-hours limit-at-mid — must not count toward real money."""
    from trading.live_gate import LiveTradingGate
    pipeline, memory = _pipeline(tmp_path)
    for _ in range(3):
        await pipeline.run(_thesis(), _candidate(), _plan(), MOMENT)
    gate = LiveTradingGate(memory=memory, criteria=VerifiedOutcomeCriteria(),
                           bankroll_usd=1000.0)
    assert memory.recent_trades(limit=10)          # orders were graded and logged
    assert gate.status()["graded_paper_trades"] == 0, "no round trip has closed"

    memory.record_closed_trade({"symbol": "AAPL", "asset_class": "equity",
                                "mode": "paper", "entry_price": 100.0,
                                "exit_price": 102.0, "quantity": 1.0,
                                "realized_return": 0.02, "realized_usd": 2.0,
                                "closed_at": 1.0})
    assert gate.status()["graded_paper_trades"] == 1


def test_a_trade_with_no_recorded_mode_does_not_count(tmp_path):
    """Unknown resolves against the operator, as everywhere else in the gate."""
    from trading.live_gate import LiveTradingGate
    memory = MemoryStore(db_path=str(tmp_path / "nm.db"))
    memory.record_closed_trade({"symbol": "AAPL", "asset_class": "equity",
                                "entry_price": 100.0, "exit_price": 102.0,
                                "quantity": 1.0, "realized_return": 0.02,
                                "realized_usd": 2.0, "closed_at": 1.0})
    gate = LiveTradingGate(memory=memory, criteria=VerifiedOutcomeCriteria(),
                           bankroll_usd=1000.0)
    assert gate.status()["graded_paper_trades"] == 0


@pytest.mark.asyncio
async def test_a_thesis_that_never_reached_the_grader_is_not_logged(tmp_path):
    """Only graded decisions belong in a ledger that counts graded decisions."""
    pipeline, memory = _pipeline(tmp_path)
    neutral = _thesis()
    neutral.consensus = Consensus(signal="neutral", confidence=50.0,
                                  summary="no edge", transcript="")
    await pipeline.run(neutral, _candidate(), _plan(), MOMENT)
    assert memory.recent_trades(limit=10) == []
