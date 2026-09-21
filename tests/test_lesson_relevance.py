"""Lessons must reach the debates they apply to, and only those.

Two findings from the self-evolving-agents literature (ANative-Lab's survey,
§ experience reuse — ReasoningBank, Agent Workflow Memory, Memento) that turn
out to describe real defects here rather than speculative upgrades:

1. **Retrieval was by recency, globally.** `recent_lesson_lines` returned the
   newest six post-mortem lessons and injected them into EVERY deliberation.
   A lesson about an equity gapping through its stop was being handed to a
   weekend BTC debate as evidence. That is wrong on its face — no hit-rate
   measurement is needed to call it a bug.

2. **Only losses were recorded.** `analyse` returned early on any win, so the
   corpus the committee reads is composed entirely of things that went wrong.
   A memory with that shape does not merely omit half the evidence, it biases
   the reader pessimistic.

Both fixes hold the module's existing bar: a finding is recorded only where
arithmetic establishes one. The win-path finding is underconfidence, which is
the exact mirror of the overconfidence finding already accepted on the loss
path, and is a calibration fault rather than a story about a stock.
"""

import pytest

from memory.store import MemoryStore
from roundtable.postmortem import (
    UNDERCONFIDENCE_THRESHOLD, Postmortem, relevant_lesson_lines,
)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "l.db"))


def _resolve(store, n):
    """Clear the injection gate — relevance is what these tests are about."""
    for i in range(n):
        store.record_thesis_outcome(f"t{i}", "AAPL", 0.01,
                                    signal="bullish", confidence=60.0, correct=True)


def _lesson(store, text, **ctx):
    store.record_lesson("*", text, context={"code": "x", **ctx})


# ---------------------------------------------------------------- relevance

def test_a_crypto_debate_does_not_read_an_equity_lesson(store):
    _resolve(store, 30)
    _lesson(store, "AAPL gapped through its stop overnight",
            symbol="AAPL", asset_class="equity")

    lines = relevant_lesson_lines(store, asset_class="crypto", symbol="BTC-USD")
    assert not any("AAPL" in l for l in lines)


def test_an_equity_debate_still_reads_its_own_lesson(store):
    _resolve(store, 30)
    _lesson(store, "AAPL gapped through its stop overnight",
            symbol="AAPL", asset_class="equity")

    lines = relevant_lesson_lines(store, asset_class="equity", symbol="MSFT")
    assert any("AAPL" in l for l in lines)


def test_the_same_symbol_outranks_a_sibling_in_its_class(store):
    """Both apply; the one about THIS instrument is the more informative."""
    _resolve(store, 30)
    _lesson(store, "sibling lesson", symbol="MSFT", asset_class="equity")
    _lesson(store, "this instrument", symbol="AAPL", asset_class="equity")

    lines = relevant_lesson_lines(store, asset_class="equity", symbol="AAPL", limit=2)
    assert lines[0] == "this instrument"


def test_a_lesson_recorded_before_scoping_existed_is_kept_but_ranked_last(store):
    """Dropping unscoped history would delete what the fund has learned; showing
    it first would reproduce the leak. Rank it below anything that matches."""
    _resolve(store, 30)
    _lesson(store, "legacy, no asset class", symbol="AAPL")
    _lesson(store, "scoped and matching", symbol="AAPL", asset_class="equity")

    lines = relevant_lesson_lines(store, asset_class="equity", symbol="AAPL", limit=2)
    assert lines == ("scoped and matching", "legacy, no asset class")


def test_the_sample_gate_still_holds_ahead_of_relevance(store):
    """Relevance does not buy a pass on the evidence gate — a well-targeted
    lesson drawn from four trades is still drawn from four trades."""
    _lesson(store, "AAPL gapped", symbol="AAPL", asset_class="equity")
    lines = relevant_lesson_lines(store, asset_class="equity", symbol="AAPL")
    assert len(lines) == 1 and "too few to generalise" in lines[0]


def test_a_broken_store_injects_nothing(store):
    class _Broken:
        def __getattr__(self, n):
            def boom(*a, **k): raise RuntimeError("gone")
            return boom
    assert relevant_lesson_lines(_Broken(), asset_class="equity", symbol="AAPL") == ()


# ---------------------------------------------------------------- wins

def _thesis(confidence, signal="bullish"):
    return {"payload": {
        "opinions": [{"seat_id": "quant", "seat_name": "Quant", "signal": signal,
                      "confidence": confidence, "failed": False, "reasoning": "r"}],
        "consensus": {"signal": signal, "confidence": confidence},
        "tally": {signal: 1},
    }}


PLAN = {"entry": 100.0, "stop": 95.0}      # 5% planned risk


def test_a_win_the_committee_barely_backed_is_recorded(store):
    """The mirror of `overconfident_loss`. Confidence drives size, so a 2R win
    taken at 52% conviction was under-sized by the same mechanism that
    over-sizes a confident loss — one fault, two signs."""
    findings = Postmortem(memory=store).analyse(
        symbol="AAPL", realized_return=0.10, thesis=_thesis(52.0), plan=PLAN,
        exit_reason="target")
    assert [f.code for f in findings] == ["underconfident_win"]


def test_a_confident_win_teaches_nothing(store):
    """The committee was right and said so. There is no fault to record, and
    inventing one here is the superstition the module already refuses."""
    assert Postmortem(memory=store).analyse(
        symbol="AAPL", realized_return=0.10, thesis=_thesis(85.0), plan=PLAN,
        exit_reason="target") == []


def test_a_trivial_win_is_not_evidence(store):
    """Same materiality bar as the loss path — a 0.1% gain says nothing about
    conviction, and every finding here ends up in a prompt."""
    assert Postmortem(memory=store).analyse(
        symbol="AAPL", realized_return=0.001, thesis=_thesis(52.0), plan=PLAN,
        exit_reason="target") == []


def test_the_threshold_sits_above_a_coin_flip(store):
    """A 50% call is not conviction at all; the finding is about a real call
    made too quietly, not about neutrality."""
    assert 50.0 < UNDERCONFIDENCE_THRESHOLD < 75.0


def test_recorded_lessons_carry_the_scope_they_apply_to(store):
    """Without this the fix above cannot work on anything written from now on."""
    Postmortem(memory=store).run(
        symbol="BTC-USD", realized_return=0.10, thesis=_thesis(52.0), plan=PLAN,
        exit_reason="target", asset_class="crypto")
    ctx = store.recent_lessons("*", limit=1)[0]["context"]
    assert ctx["asset_class"] == "crypto" and ctx["symbol"] == "BTC-USD"


def test_losses_are_still_analysed_exactly_as_before(store):
    """The win path must not have moved the loss path."""
    findings = Postmortem(memory=store).analyse(
        symbol="AAPL", realized_return=-0.10, thesis=_thesis(90.0), plan=PLAN)
    assert "overconfident_loss" in [f.code for f in findings]
