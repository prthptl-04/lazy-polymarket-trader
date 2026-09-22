"""Replay the committee over past equity sessions, scored against what happened.

The question this answers: can these seats trade equities, and can we know
before risking a session on it? Live calibration needs 30 resolved outcomes and
produces roughly one deliberation per cycle — days of waiting. The same seats
can be put in front of past sessions tonight, and the market has already
answered.

Three ways a replay lies, and what is done about each:

**Look-ahead in the bars.** The candidate is built from a strict slice ending at
the decision bar. The future is unreachable by construction, not by convention.

**Look-ahead in the evidence.** News, fundamentals, catalysts and post-mortem
lessons cannot be reliably reconstructed as-of a past date, so they are not
supplied at all and the block says NOT AVAILABLE. That UNDERSTATES what the
committee knows, which is the safe direction: a replay that flattered the seats
with tomorrow's headlines would be worse than no replay.

**Contaminating the live record.** Replay outcomes are marked, kept out of the
confidence-shrink fit, and cannot count toward the 50 paper trades rule #13
requires. They calibrate seats; they do not earn a live flip.
"""

import pytest

from backtest.committee import REPLAY_NOTE, ReplayCandidate, score_replay


def test_the_candidate_never_sees_a_bar_past_its_decision_point():
    """Look-ahead, closed by construction rather than by care."""
    bars = [type("B", (), {"high": 10 + i, "low": 9 + i, "close": 9.5 + i,
                           "open": 9.5 + i})() for i in range(40)]
    c = ReplayCandidate.build("AAPL", bars, at=20)
    assert len(c.bars) == 21
    assert c.bars[-1] is bars[20]


def test_the_forward_return_comes_from_bars_the_candidate_never_saw():
    bars = [type("B", (), {"high": 10, "low": 9, "close": 100.0 + i,
                           "open": 100.0 + i})() for i in range(40)]
    c = ReplayCandidate.build("AAPL", bars, at=20, horizon=1)
    # decision close 120, next close 121
    assert c.forward_return == pytest.approx(1 / 120, rel=1e-6)


def test_a_decision_too_near_the_end_has_no_forward_return():
    """Without a future bar there is nothing to score against, and scoring it
    as flat would add a fabricated sample."""
    bars = [type("B", (), {"high": 10, "low": 9, "close": 100.0, "open": 100.0})()
            for _ in range(10)]
    assert ReplayCandidate.build("AAPL", bars, at=9, horizon=1) is None


def test_scoring_is_by_direction_not_by_sign():
    assert score_replay("bearish", -0.04) == pytest.approx(0.04)
    assert score_replay("bullish", 0.04) == pytest.approx(0.04)


def test_a_neutral_call_scores_nothing():
    assert score_replay("neutral", 0.09) is None


def test_the_evidence_declares_what_it_could_not_reconstruct():
    """Silently omitting news would let a seat read its absence as a quiet
    tape. The block has to say the source was never fetched."""
    bars = [type("B", (), {"high": 10 + i, "low": 9 + i, "close": 9.5 + i,
                           "open": 9.5 + i})() for i in range(40)]
    c = ReplayCandidate.build("AAPL", bars, at=20)
    block = c.as_candidate().evidence_block()
    assert "NOT AVAILABLE" in block or "not available" in block.lower()


def test_a_replay_outcome_is_marked():
    assert REPLAY_NOTE.startswith("replay:")


def test_replay_outcomes_cannot_fit_the_confidence_shrink(tmp_path):
    """They are scored on a raw forward return, not on a position bounded by a
    stop, so they flatter the trades they would have become."""
    from memory.store import MemoryStore
    from roundtable.calibration import fit_confidence_shrink
    s = MemoryStore(db_path=str(tmp_path / "r.db"))
    for i in range(40):
        s.record_thesis_outcome(f"r{i}", "AAPL", 0.05, signal="bullish",
                                confidence=60.0, correct=True, notes=REPLAY_NOTE)
    assert not fit_confidence_shrink(s.resolved_outcomes(limit=100)).usable


def test_replay_outcomes_cannot_open_the_live_gate(tmp_path):
    """Rule #13 wants 50 paper TRADES. A replay is not a trade."""
    from memory.store import MemoryStore
    from trading.live_gate import LiveTradingGate
    s = MemoryStore(db_path=str(tmp_path / "r.db"))
    for i in range(60):
        s.record_thesis_outcome(f"r{i}", "AAPL", 0.05, signal="bullish",
                                confidence=60.0, correct=True, notes=REPLAY_NOTE)
    assert LiveTradingGate(memory=s, bankroll_usd=500.0).status()["graded_paper_trades"] == 0


def test_seat_scoring_does_use_them(tmp_path):
    """The point of the exercise: seats calibrate on direction, which needs no
    position."""
    from memory.store import MemoryStore
    from roundtable.calibration import score_seats
    s = MemoryStore(db_path=str(tmp_path / "r.db"))
    for i in range(35):
        s.save_deliberation(
            f"r{i}", "AAPL", "equity", "complete",
            {"opinions": [{"seat_id": "quant", "seat_name": "Quant",
                           "signal": "bullish", "confidence": 60.0, "failed": False}],
             "consensus": {"signal": "bullish", "confidence": 60.0}},
            signal="bullish", confidence=60.0)
        s.record_thesis_outcome(f"r{i}", "AAPL", 0.05, signal="bullish",
                                confidence=60.0, correct=True, notes=REPLAY_NOTE)
    card = score_seats(s.recent_deliberations(limit=100),
                       {o["thesis_id"]: o for o in s.resolved_outcomes(limit=100)})
    quant = next(x for x in card.seats if x.seat_id == "quant")
    assert quant.samples >= 30 and quant.is_scored
