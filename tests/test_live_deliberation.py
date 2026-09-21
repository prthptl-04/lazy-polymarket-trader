"""Watch the committee think, instead of waiting for it to finish.

A deliberation is seven LLM calls and takes 30-60 seconds. The UI saw none of
it: `on_opinion` existed on RoundTable, was covered by tests, and was wired to
nothing — so the panel showed the previous thesis until the whole debate
completed and was persisted, then jumped. A minute of black box, then an answer
with no visible reasoning behind it.

Idea taken from trynhexagon/aleph-trading, which streams its agents' debate as
it happens rather than presenting the memo at the end. None of its code is used
— it is a LangGraph/SSE stack and this is a polled FastAPI one — only the
observation that the reasoning is the interesting part and arrives long before
the conclusion.

Two things this must not do: block the deliberation on a display concern, and
present an in-progress debate as though it were a decision.
"""

import pytest

from dashboard.runtime import DashboardRuntime
from memory.store import MemoryStore
from roundtable.types import SeatOpinion


def _rt(tmp_path):
    return DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "l.db")))


def _op(seat_id, signal="bullish", confidence=70.0, failed=False):
    return SeatOpinion(seat_id=seat_id, seat_name=seat_id.title(), signal=signal,
                       confidence=confidence, reasoning="because", failed=failed)


def test_nothing_in_flight_reports_nothing(tmp_path):
    assert _rt(tmp_path).live_debate()["opinions"] == []


def test_seats_appear_as_they_answer(tmp_path):
    """The point: the panel fills in over the minute rather than jumping."""
    rt = _rt(tmp_path)
    rt.begin_debate("ETH")
    rt.record_opinion(_op("quant"))
    assert [o["seat_id"] for o in rt.live_debate()["opinions"]] == ["quant"]
    rt.record_opinion(_op("risk", signal="bearish"))
    assert [o["seat_id"] for o in rt.live_debate()["opinions"]] == ["quant", "risk"]


def test_the_symbol_under_debate_is_reported(tmp_path):
    rt = _rt(tmp_path)
    rt.begin_debate("BTC")
    assert rt.live_debate()["symbol"] == "BTC"


def test_an_in_flight_debate_is_marked_as_undecided(tmp_path):
    """It must never read as a verdict. Four seats in is not a decision, and a
    panel that implies otherwise would be worse than no panel."""
    rt = _rt(tmp_path)
    rt.begin_debate("ETH")
    rt.record_opinion(_op("quant"))
    assert rt.live_debate()["in_progress"] is True
    assert rt.live_debate().get("consensus") is None


def test_a_finished_debate_stops_being_in_progress(tmp_path):
    rt = _rt(tmp_path)
    rt.begin_debate("ETH")
    rt.record_opinion(_op("quant"))
    rt.finish_debate()
    assert rt.live_debate()["in_progress"] is False


def test_a_new_debate_replaces_the_last(tmp_path):
    """One committee, one room. Carrying opinions between symbols would
    attribute one instrument's argument to another."""
    rt = _rt(tmp_path)
    rt.begin_debate("ETH"); rt.record_opinion(_op("quant"))
    rt.begin_debate("BTC")
    live = rt.live_debate()
    assert live["symbol"] == "BTC" and live["opinions"] == []


def test_an_abstention_is_shown_as_one(tmp_path):
    """A failed seat is the most informative thing on the panel — the table was
    thinner than its seat count suggests."""
    rt = _rt(tmp_path)
    rt.begin_debate("ETH")
    rt.record_opinion(_op("risk", failed=True))
    assert rt.live_debate()["opinions"][0]["failed"] is True


def test_the_seat_count_expected_is_reported(tmp_path):
    """Two of six answered reads very differently from two of two."""
    rt = _rt(tmp_path)
    rt.begin_debate("ETH")
    rt.record_opinion(_op("quant"))
    live = rt.live_debate()
    assert live["expected_seats"] >= 6
    assert live["answered"] == 1


@pytest.mark.asyncio
async def test_the_hook_never_breaks_a_deliberation(tmp_path):
    """A display concern must not be able to stop the fund thinking."""
    from roundtable.engine import RoundTable
    import json

    class _Block:
        def __init__(self, t): self.text, self.type = t, "text"

    class _Client:
        class _M:
            def create(self, **kw):
                return type("R", (), {"content": [_Block(json.dumps(
                    {"signal": "bullish", "confidence": 70, "reasoning": "r",
                     "summary": "s", "dissent": "d", "transcript": "t"}))]})()
        messages = _M()

    def explode(_):
        raise RuntimeError("the panel is on fire")

    from trading.candidate_builder import build_candidate
    from finance.exits import Bar
    c = build_candidate(symbol="AAPL", bars=[Bar(high=101, low=99, close=100)] * 30,
                        price=100.0, asset_class="equity", session="regular",
                        spread_bps=4, returns=[0.004] * 30,
                        dollar_volumes=[5e8] * 30).candidate

    thesis = await RoundTable(client=_Client(), on_opinion=explode).deliberate(c)
    assert thesis.consensus is not None
