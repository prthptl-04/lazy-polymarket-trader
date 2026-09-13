"""Round-table deliberation.

- Acceptance: five seats speak, the chair synthesizes, the thesis persists.
- Blind: round 1 seats must NOT see each other (that is the independence);
  the Devil's Advocate MUST see them; a failed seat abstains rather than
  silently voting; the chair failing falls back to a capped tally.
- Edge: fenced JSON, thinking blocks, garbage output, timeouts.
"""

import asyncio
import json

import pytest

from memory.store import MemoryStore
from roundtable.engine import RoundTable, _extract_text, _parse_json
from roundtable.seats import ROUND_ONE_SEATS, SEATS_BY_ID
from roundtable.types import Candidate, SeatOpinion, Thesis


# ---------------- fakes ----------------

class _Block:
    def __init__(self, text, type_="text"):
        self.text = text
        self.type = type_


class _Response:
    def __init__(self, blocks):
        self.content = blocks


class _FakeClient:
    """Records every (system, user) pair and replies from a scripted map."""

    def __init__(self, reply_for=None, default=None, raises_on=None):
        self.calls = []
        self.reply_for = reply_for or {}
        self.default = default or _opinion_json("neutral", 50)
        self.raises_on = raises_on or set()

        outer = self

        class _Messages:
            def create(self, **kwargs):
                system = kwargs["system"]
                system_text = (
                    system[0]["text"] if isinstance(system, list) else str(system)
                )
                user = kwargs["messages"][0]["content"]
                outer.calls.append({"system": system_text, "user": user})
                for marker, exc in outer.raises_on:
                    if marker in system_text:
                        raise exc
                for marker, reply in outer.reply_for.items():
                    if marker in system_text:
                        return _Response([_Block(reply)])
                return _Response([_Block(outer.default)])

        self.messages = _Messages()


def _opinion_json(signal, confidence, reasoning="because the evidence says so"):
    return json.dumps({
        "signal": signal, "confidence": confidence, "reasoning": reasoning,
        "key_points": ["a point"], "concerns": ["a concern"],
    })


def _chair_json(signal="bullish", confidence=70):
    return json.dumps({
        "signal": signal, "confidence": confidence,
        "summary": "The committee resolved.",
        "dissent": "Risk flagged the stop.",
        "transcript": "[Fundamental Analyst]: solid.\n[Risk Manager]: careful.",
    })


def _candidate(**kw):
    base = dict(
        symbol="AAPL", asset_class="equity", price=231.4, session="regular",
        spread_bps=8, atr=4.2, cvar_pct=0.05, altman_z=3.4, altman_zone="safe",
        piotroski_f=8, entry=231.4, stop=222.0, target=250.0,
        sentiment_notes=("chatter is positive",),
    )
    base.update(kw)
    return Candidate(**base)


# ---------------- happy path ----------------

@pytest.mark.asyncio
async def test_full_deliberation_produces_a_consensus():
    client = _FakeClient(reply_for={"Chair of an investment committee": _chair_json()})
    rt = RoundTable(client=client)

    thesis = await rt.deliberate(_candidate())

    assert len(thesis.opinions) == 6          # 5 round-one + devil's advocate
    assert thesis.consensus is not None
    assert thesis.consensus.signal == "bullish"
    assert thesis.status == "complete"
    assert thesis.consensus.synthesized_by_llm


@pytest.mark.asyncio
async def test_seven_llm_calls_per_candidate():
    client = _FakeClient(reply_for={"Chair of an investment committee": _chair_json()})
    await RoundTable(client=client).deliberate(_candidate())
    assert len(client.calls) == 7             # 6 seats + 1 chair


@pytest.mark.asyncio
async def test_every_seat_is_consulted():
    client = _FakeClient(reply_for={"Chair of an investment committee": _chair_json()})
    thesis = await RoundTable(client=client).deliberate(_candidate())
    assert {o.seat_id for o in thesis.opinions} == set(SEATS_BY_ID)


# ---------------- independence (the point of the design) ----------------

@pytest.mark.asyncio
async def test_round_one_seats_cannot_see_each_other():
    """Parallel round 1 is what makes seat independence real."""
    client = _FakeClient(reply_for={"Chair of an investment committee": _chair_json()})
    await RoundTable(client=client).deliberate(_candidate())

    round_one_names = {s.name for s in ROUND_ONE_SEATS}
    for call in client.calls:
        if "Devil's Advocate" in call["system"] or "Chair" in call["system"]:
            continue
        # No round-one prompt may contain another seat's rendered opinion.
        assert "--- THE OTHER SEATS ---" not in call["user"]
        for name in round_one_names:
            assert f"[{name}]" not in call["user"]


@pytest.mark.asyncio
async def test_devils_advocate_does_see_the_others():
    client = _FakeClient(reply_for={"Chair of an investment committee": _chair_json()})
    await RoundTable(client=client).deliberate(_candidate())

    da_call = next(c for c in client.calls if "Devil's Advocate" in c["system"])
    assert "--- THE OTHER SEATS ---" in da_call["user"]
    assert "[Risk Manager]" in da_call["user"]


@pytest.mark.asyncio
async def test_chair_sees_all_seat_positions():
    client = _FakeClient(reply_for={"Chair of an investment committee": _chair_json()})
    await RoundTable(client=client).deliberate(_candidate())

    chair = next(c for c in client.calls if "Chair of an investment" in c["system"])
    assert "--- SEAT POSITIONS ---" in chair["user"]
    assert "[Devil's Advocate]" in chair["user"]


# ---------------- failure handling ----------------

@pytest.mark.asyncio
async def test_failed_seat_abstains_rather_than_voting():
    """A dead seat must never be silently counted as agreement."""
    client = _FakeClient(
        reply_for={"Chair of an investment committee": _chair_json()},
        raises_on=[("Quantitative Analyst", RuntimeError("api down"))],
    )
    thesis = await RoundTable(client=client).deliberate(_candidate())

    quant = next(o for o in thesis.opinions if o.seat_id == "quant")
    assert quant.failed
    assert quant.confidence == 0.0
    assert "api down" in quant.error
    # The abstention is excluded from the tally entirely.
    assert sum(thesis.tally().values()) == 5


@pytest.mark.asyncio
async def test_unparseable_seat_response_abstains():
    client = _FakeClient(
        reply_for={
            "Fundamental Analyst": "I have thoughts but no JSON",
            "Chair of an investment committee": _chair_json(),
        },
    )
    thesis = await RoundTable(client=client).deliberate(_candidate())
    analyst = next(o for o in thesis.opinions if o.seat_id == "analyst")
    assert analyst.failed and "unparseable" in analyst.error


@pytest.mark.asyncio
async def test_chair_failure_falls_back_to_a_capped_tally():
    client = _FakeClient(
        default=_opinion_json("bullish", 95),
        raises_on=[("Chair of an investment committee", RuntimeError("chair down"))],
    )
    thesis = await RoundTable(client=client).deliberate(_candidate())

    c = thesis.consensus
    assert c.signal == "bullish"
    assert not c.synthesized_by_llm
    # Seats said 95; a tally must not inherit that conviction.
    assert c.confidence <= 50.0
    assert "vote count, not a synthesis" in c.summary


@pytest.mark.asyncio
async def test_all_seats_failing_yields_no_basis_for_a_view():
    client = _FakeClient(raises_on=[("investment committee", RuntimeError("total outage"))])
    thesis = await RoundTable(client=client).deliberate(_candidate())

    assert thesis.consensus.signal == "neutral"
    assert thesis.consensus.confidence == 0.0


@pytest.mark.asyncio
async def test_seat_timeout_abstains():
    class _SlowClient(_FakeClient):
        def __init__(self):
            super().__init__()
            outer = self

            class _Messages:
                def create(self, **kwargs):
                    import time
                    time.sleep(0.3)
                    return _Response([_Block(_opinion_json("bullish", 60))])

            self.messages = _Messages()

    rt = RoundTable(client=_SlowClient(), seat_timeout_seconds=0.05)
    thesis = await rt.deliberate(_candidate())
    assert all(o.failed for o in thesis.opinions)


# ---------------- persistence + resume ----------------

@pytest.mark.asyncio
async def test_thesis_persists_and_completes(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "rt.db"))
    client = _FakeClient(reply_for={"Chair of an investment committee": _chair_json()})

    thesis = await RoundTable(client=client, memory=store).deliberate(_candidate())

    saved = store.get_deliberation(thesis.thesis_id)
    assert saved["status"] == "complete"
    assert saved["signal"] == "bullish"
    assert len(saved["payload"]["opinions"]) == 6
    assert store.unfinished_deliberations() == []


@pytest.mark.asyncio
async def test_interrupted_deliberation_stays_resumable(tmp_path):
    """A STOP mid-debate must leave a row, not lose the work."""
    store = MemoryStore(db_path=str(tmp_path / "rt.db"))
    client = _FakeClient(
        raises_on=[("Devil's Advocate", RuntimeError("stopped"))],
        reply_for={"Chair of an investment committee": _chair_json()},
    )
    rt = RoundTable(client=client, memory=store)

    # Simulate a hard stop by cancelling after round 1 persists.
    task = asyncio.create_task(rt.deliberate(_candidate()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    unfinished = store.unfinished_deliberations()
    assert len(unfinished) == 1
    assert unfinished[0]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_persistence_failure_does_not_abort_the_deliberation():
    class _BrokenStore:
        def save_deliberation(self, **kw):
            raise RuntimeError("disk full")

    client = _FakeClient(reply_for={"Chair of an investment committee": _chair_json()})
    thesis = await RoundTable(client=client, memory=_BrokenStore()).deliberate(_candidate())
    assert thesis.consensus is not None


# ---------------- callbacks ----------------

@pytest.mark.asyncio
async def test_opinion_callback_fires_per_seat():
    seen = []
    client = _FakeClient(reply_for={"Chair of an investment committee": _chair_json()})
    rt = RoundTable(client=client, on_opinion=seen.append)
    await rt.deliberate(_candidate())
    assert len(seen) == 6


@pytest.mark.asyncio
async def test_failing_callback_does_not_kill_the_deliberation():
    def boom(_):
        raise RuntimeError("ui exploded")

    client = _FakeClient(reply_for={"Chair of an investment committee": _chair_json()})
    rt = RoundTable(client=client, on_opinion=boom, on_thesis=boom)
    thesis = await rt.deliberate(_candidate())
    assert thesis.consensus is not None


# ---------------- evidence discipline ----------------

def test_missing_evidence_is_named_not_hidden():
    block = Candidate(symbol="XYZ").evidence_block()
    assert "NOT AVAILABLE" in block
    assert "Altman Z" in block


def test_evidence_block_carries_the_numbers():
    block = _candidate().evidence_block()
    assert "Altman Z: 3.4" in block
    assert "Piotroski F: 8" in block
    assert "Reward:risk" in block


def test_r_multiple_computed_from_the_plan():
    c = _candidate(entry=100.0, stop=96.0, target=108.0)
    assert c.r_multiple == pytest.approx(2.0)


def test_r_multiple_none_without_a_plan():
    assert _candidate(stop=None).r_multiple is None


# ---------------- thesis helpers ----------------

def test_tally_excludes_failed_seats():
    t = Thesis(symbol="X")
    t.opinions = [
        SeatOpinion("a", "A", "bullish", 70, "r"),
        SeatOpinion("b", "B", "bearish", 60, "r"),
        SeatOpinion("c", "C", "neutral", 0, "r", failed=True),
    ]
    assert t.tally() == {"bullish": 1, "bearish": 1, "neutral": 0}


def test_unanimity_is_detectable():
    t = Thesis(symbol="X")
    t.opinions = [SeatOpinion(str(i), str(i), "bullish", 70, "r") for i in range(4)]
    assert not t.has_dissent()

    t.opinions.append(SeatOpinion("d", "D", "bearish", 60, "r"))
    assert t.has_dissent()


# ---------------- parsing ----------------

def test_parse_fenced_json():
    assert _parse_json('```json\n{"signal": "bullish"}\n```')["signal"] == "bullish"


def test_parse_json_with_surrounding_prose():
    assert _parse_json('Sure!\n{"signal": "bearish"}\nHope that helps')["signal"] == "bearish"


def test_parse_garbage_returns_none():
    assert _parse_json("no json here") is None
    assert _parse_json("") is None
    assert _parse_json("[1,2,3]") is None      # not an object


def test_extract_text_skips_thinking_blocks():
    resp = _Response([_Block("internal musing", "thinking"), _Block('{"signal":"neutral"}')])
    assert _extract_text(resp) == '{"signal":"neutral"}'


def test_extract_text_handles_empty_content():
    assert _extract_text(_Response([])) == ""


@pytest.mark.asyncio
async def test_out_of_range_confidence_is_clamped():
    client = _FakeClient(
        default=json.dumps({"signal": "bullish", "confidence": 5000, "reasoning": "r"}),
        reply_for={"Chair of an investment committee": _chair_json()},
    )
    thesis = await RoundTable(client=client).deliberate(_candidate())
    assert all(o.confidence <= 100.0 for o in thesis.opinions)


@pytest.mark.asyncio
async def test_invalid_signal_becomes_neutral():
    client = _FakeClient(
        default=json.dumps({"signal": "MOON", "confidence": 90, "reasoning": "r"}),
        reply_for={"Chair of an investment committee": _chair_json()},
    )
    thesis = await RoundTable(client=client).deliberate(_candidate())
    round_one = [o for o in thesis.opinions if o.seat_id != "devils_advocate"]
    assert all(o.signal == "neutral" for o in round_one)
