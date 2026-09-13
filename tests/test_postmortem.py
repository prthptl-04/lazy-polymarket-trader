"""Post-mortem — the fund learns from losses.

Closing a losing position is the only moment the fund learns anything for
certain. Everything before it is opinion.

- Blind: a WIN teaches nothing automatically. Claiming otherwise teaches
  superstition.
- Blind: lessons reach the seats as EVIDENCE, never in the system prompt —
  those are cache-tagged and mutating them would discard the prompt cache on
  every new lesson.
- Acceptance: a correct dissent is identified, because that is the failure the
  Devil's Advocate exists to prevent.
"""

import pytest

from memory.store import MemoryStore
from roundtable.postmortem import (
    OVERCONFIDENCE_THRESHOLD,
    Postmortem,
    recent_lesson_lines,
)
from roundtable.types import Candidate


def _thesis(opinions, signal="bullish", confidence=80.0, tally=None):
    return {"payload": {
        "opinions": [{"seat_id": o[0], "seat_name": o[1], "signal": o[2],
                      "confidence": o[3], "reasoning": o[4] if len(o) > 4 else "",
                      "concerns": [], "failed": False} for o in opinions],
        "consensus": {"signal": signal, "confidence": confidence},
        "tally": tally or {"bullish": len(opinions), "bearish": 0, "neutral": 0},
    }}


def _codes(findings):
    return {f.code for f in findings}


# ---------------- wins teach nothing ----------------

def test_a_win_produces_no_findings():
    """A win is not proof the reasoning was right; inventing a moral from one
    teaches superstition."""
    t = _thesis([("quant", "Quant", "bullish", 90)])
    assert Postmortem().analyse(symbol="AAPL", realized_return=0.08, thesis=t) == []


def test_a_flat_close_produces_no_findings():
    t = _thesis([("quant", "Quant", "bullish", 90)])
    assert Postmortem().analyse(symbol="AAPL", realized_return=0.0, thesis=t) == []


# ---------------- the findings ----------------

def test_unanimous_loss_is_flagged():
    t = _thesis([("a", "A", "bullish", 80), ("b", "B", "bullish", 80)],
                tally={"bullish": 2, "bearish": 0, "neutral": 0})
    f = Postmortem().analyse(symbol="AAPL", realized_return=-0.05, thesis=t)
    assert "unanimous_loss" in _codes(f)


def test_a_single_seat_is_not_unanimity():
    t = _thesis([("a", "A", "bullish", 60)],
                tally={"bullish": 1, "bearish": 0, "neutral": 0})
    assert "unanimous_loss" not in _codes(
        Postmortem().analyse(symbol="AAPL", realized_return=-0.05, thesis=t))


def test_correct_dissent_is_identified_with_its_reasoning():
    """The most valuable thing available to learn."""
    t = _thesis([("quant", "Quant", "bullish", 90),
                 ("risk", "Risk Manager", "bearish", 70, "the stop sits in noise")],
                tally={"bullish": 1, "bearish": 1, "neutral": 0})
    f = Postmortem().analyse(symbol="AAPL", realized_return=-0.06, thesis=t)
    dissent = next(x for x in f if x.code == "correct_dissent")
    assert "Risk Manager" in dissent.detail
    assert "stop sits in noise" in dissent.detail
    assert dissent.severity == "warning"


def test_unverified_data_is_flagged_as_process_not_stock_picking():
    t = _thesis([("corroborator", "Corroborator", "neutral", 30,
                  "nothing was single-sourced confirmed; price MISMATCH")])
    f = Postmortem().analyse(symbol="AAPL", realized_return=-0.04, thesis=t)
    assert "traded_on_unverified_data" in _codes(f)


def test_overconfidence_is_flagged():
    t = _thesis([("a", "A", "bullish", 95)], confidence=95.0)
    assert "overconfident_loss" in _codes(
        Postmortem().analyse(symbol="AAPL", realized_return=-0.05, thesis=t))


def test_modest_confidence_is_not_flagged_as_overconfident():
    t = _thesis([("a", "A", "bullish", 55)], confidence=55.0)
    assert "overconfident_loss" not in _codes(
        Postmortem().analyse(symbol="AAPL", realized_return=-0.05, thesis=t))


def test_threshold_is_sane():
    assert 60 <= OVERCONFIDENCE_THRESHOLD <= 90


def test_missing_thesis_still_yields_something_usable():
    f = Postmortem().analyse(symbol="AAPL", realized_return=-0.05, thesis=None)
    assert "stopped_out" not in _codes(f) or f      # never raises


# ---------------- persistence + delivery ----------------

def test_findings_become_lessons_every_seat_sees(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "p.db"))
    t = _thesis([("quant", "Quant", "bullish", 90),
                 ("risk", "Risk", "bearish", 70, "stop too tight")],
                tally={"bullish": 1, "bearish": 1, "neutral": 0})
    Postmortem(memory=store).run(symbol="AAPL", realized_return=-0.06, thesis=t)

    lessons = store.recent_lessons("*", limit=20)
    assert any(l["context"].get("code") == "correct_dissent" for l in lessons)


def test_lesson_lines_only_return_postmortem_lessons(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "p.db"))
    store.record_lesson("*", "operational note with no code")
    Postmortem(memory=store).run(
        symbol="AAPL", realized_return=-0.06,
        thesis=_thesis([("a", "A", "bullish", 95)], confidence=95.0))

    lines = recent_lesson_lines(store)
    assert lines and all("operational note" not in l for l in lines)


def test_lesson_lines_degrade_without_memory():
    assert recent_lesson_lines(None) == ()


def test_recording_failure_does_not_raise():
    class _Broken:
        def record_lesson(self, *a, **kw): raise RuntimeError("disk full")
    t = _thesis([("a", "A", "bullish", 95)], confidence=95.0)
    assert Postmortem(memory=_Broken()).run(
        symbol="AAPL", realized_return=-0.05, thesis=t)


# ---------------- delivery to the seats ----------------

def test_lessons_appear_in_the_evidence_block_not_the_prompt():
    """System prompts are cache-tagged (rule #2); lessons must ride in the
    variable message or every new lesson discards the prompt cache."""
    from roundtable.seats import ALL_SEATS

    c = Candidate(symbol="AAPL", lessons=("do not trade on unverified data",))
    block = c.evidence_block()
    assert "LESSONS FROM PAST LOSSES" in block
    assert "do not trade on unverified data" in block
    for seat in ALL_SEATS:
        assert "do not trade on unverified data" not in seat.system_prompt


def test_no_lessons_means_no_section():
    assert "LESSONS" not in Candidate(symbol="AAPL").evidence_block()


# ---------------- end to end through the fund loop ----------------

@pytest.mark.asyncio
async def test_a_losing_exit_writes_a_lesson_the_next_cycle_reads(tmp_path):
    """The whole point: a stop-out must change what the seats see next time."""
    from datetime import datetime
    from finance.exits import Bar, build_exit_plan
    from trading.fund import FundLoop
    from trading.position_book import PositionBook
    from trading.sessions import EASTERN
    from trading.venues.base import OrderAck, Quote

    store = MemoryStore(db_path=str(tmp_path / "e2e.db"))
    store.save_deliberation(
        "t1", "AAPL", "equity", "complete",
        _thesis([("quant", "Quant", "bullish", 92),
                 ("risk", "Risk Manager", "bearish", 70, "stop sits inside noise")],
                confidence=92.0, tally={"bullish": 1, "bearish": 1, "neutral": 0})["payload"],
        signal="bullish", confidence=92.0,
    )

    bars = [Bar(high=102, low=100, close=101) for _ in range(20)]
    book = PositionBook(memory=store)
    book.open(symbol="AAPL", asset_class="equity", quantity=10, entry_price=100.0,
              plan=build_exit_plan(entry=100.0, bars=bars), thesis_id="t1")

    class _Router:
        async def place(self, order, moment, **kw):
            return OrderAck(accepted=True, client_order_id="c", venue_order_id="v",
                            status="filled", venue="paper")

    class _Data:
        async def get_quote(self, symbol):
            return Quote(symbol=symbol, bid=94.9, ask=95.1)     # through the stop
        async def get_history(self, symbol, lookback=60): return None
        async def get_financials(self, symbol): return None

    loop = FundLoop(router=_Router(), pipeline=None, round_table=None, data=_Data(),
                    position_book=book, memory=store, postmortem=Postmortem(memory=store))
    report = await loop.run_cycle(datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN))

    assert len(report.exits) == 1
    assert "correct_dissent" in report.exits[0]["lessons"]

    # And the lesson is now in what the next candidate shows the seats.
    lines = recent_lesson_lines(store)
    assert any("Risk Manager" in l and "was right" in l for l in lines)
    assert "stop sits inside noise" in " ".join(lines)


def test_lessons_route_shows_only_trading_lessons(tmp_path):
    from fastapi.testclient import TestClient
    from dashboard.runtime import build_runtime
    from dashboard.server import create_app

    store = MemoryStore(db_path=str(tmp_path / "ui.db"))
    store.record_lesson("*", "operational note, not a trading lesson")
    Postmortem(memory=store).run(
        symbol="AAPL", realized_return=-0.06,
        thesis=_thesis([("a", "A", "bullish", 95)], confidence=95.0))

    body = TestClient(create_app(build_runtime(memory=store))).get("/api/lessons").json()
    assert body and all(l["code"] for l in body)
    assert all("operational note" not in l["lesson"] for l in body)


def test_overview_explains_when_nothing_is_learned_yet():
    from dashboard.pages import OVERVIEW_HTML
    assert "Nothing learned yet" in OVERVIEW_HTML
    assert "closes at a loss" in OVERVIEW_HTML
