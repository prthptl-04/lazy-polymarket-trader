"""Round-table scoring and confidence calibration.

This is the loop that lets the committee improve. The tests that matter are
the ones enforcing that it scores honestly:

- Blind: a seat is scored on ITS OWN call, not the committee's — otherwise the
  scorecard rewards conformity, which is the failure the Devil's Advocate
  exists to prevent.
- Blind: a fit refuses to produce a number from too few samples.
- Edge: neutral calls are excluded rather than counted as losses; abstentions
  are tracked separately.
"""

import pytest

from memory.store import MemoryStore
from roundtable.calibration import (
    MIN_SAMPLES_FOR_FIT,
    direction_was_right,
    fit_confidence_shrink,
    score_seats,
)


def _delib(thesis_id, opinions, consensus_signal="bullish", consensus_conf=70.0):
    return {
        "thesis_id": thesis_id,
        "symbol": "AAPL",
        "payload": {
            "opinions": [
                {
                    "seat_id": o[0], "seat_name": o[0].title(),
                    "signal": o[1], "confidence": o[2],
                    "failed": len(o) > 3 and o[3],
                }
                for o in opinions
            ],
            "consensus": {"signal": consensus_signal, "confidence": consensus_conf},
        },
    }


def _outcome(thesis_id, realized, confidence=70.0, correct=True):
    return {
        "thesis_id": thesis_id, "symbol": "AAPL", "signal": "bullish",
        "confidence": confidence, "realized_return": realized, "correct": correct,
    }


# ---------------- direction ----------------

@pytest.mark.parametrize("signal,realized,expected", [
    ("bullish", 0.05, True),
    ("bullish", -0.05, False),
    ("bearish", -0.05, True),
    ("bearish", 0.05, False),
])
def test_direction_scoring(signal, realized, expected):
    assert direction_was_right(signal, realized) is expected


def test_neutral_has_no_direction_to_be_wrong_about():
    """Punishing 'I don't know' teaches seats to guess."""
    assert direction_was_right("neutral", 0.05) is None
    assert direction_was_right("neutral", -0.05) is None


# ---------------- seat scoring ----------------

def test_seat_scored_on_its_own_call_not_the_committee():
    """A bearish seat inside a bullish committee that LOST was right."""
    delibs = [_delib("t1", [("bull_seat", "bullish", 80), ("bear_seat", "bearish", 80)],
                     consensus_signal="bullish")]
    outcomes = {"t1": _outcome("t1", realized=-0.10)}   # price fell

    card = score_seats(delibs, outcomes)
    by_id = {s.seat_id: s for s in card.seats}

    assert by_id["bear_seat"].hit_rate == 1.0     # dissenter was right
    assert by_id["bull_seat"].hit_rate == 0.0
    assert card.committee.hit_rate == 0.0         # committee was wrong


def test_abstentions_tracked_separately_from_wrong_calls():
    delibs = [_delib("t1", [("a", "neutral", 0, True), ("b", "bullish", 70)])]
    card = score_seats(delibs, {"t1": _outcome("t1", 0.05)})
    by_id = {s.seat_id: s for s in card.seats}

    assert by_id["a"].abstentions == 1
    assert by_id["a"].samples == 0               # not scored as a miss
    assert by_id["b"].samples == 1


def test_neutral_calls_are_excluded_from_samples():
    delibs = [_delib("t1", [("a", "neutral", 50), ("b", "bullish", 70)])]
    card = score_seats(delibs, {"t1": _outcome("t1", 0.05)})
    by_id = {s.seat_id: s for s in card.seats}
    assert by_id["a"].samples == 0


def test_unresolved_theses_are_ignored():
    delibs = [_delib("t1", [("a", "bullish", 70)]),
              _delib("t2", [("a", "bullish", 70)])]
    card = score_seats(delibs, {"t1": _outcome("t1", 0.05)})
    assert card.resolved == 1
    assert card.seats[0].samples == 1


def test_no_outcomes_yields_an_empty_scorecard():
    card = score_seats([_delib("t1", [("a", "bullish", 70)])], {})
    assert card.resolved == 0
    assert card.committee is None


# ---------------- Brier + overconfidence ----------------

def test_confident_and_wrong_scores_worse_than_unsure_and_wrong():
    """Brier punishes confident wrongness; accuracy would not tell them apart."""
    loud = score_seats(
        [_delib("t1", [("loud", "bullish", 99)])], {"t1": _outcome("t1", -0.1)}
    ).seats[0]
    quiet = score_seats(
        [_delib("t1", [("quiet", "bullish", 51)])], {"t1": _outcome("t1", -0.1)}
    ).seats[0]

    assert loud.brier > quiet.brier


def test_overconfidence_is_stated_minus_realized():
    # Two calls at 90 confidence, one right → 50% hit rate, +40 overconfident.
    delibs = [
        _delib("t1", [("a", "bullish", 90)]),
        _delib("t2", [("a", "bullish", 90)]),
    ]
    outcomes = {"t1": _outcome("t1", 0.05), "t2": _outcome("t2", -0.05)}
    seat = score_seats(delibs, outcomes).seats[0]

    assert seat.hit_rate == pytest.approx(0.5)
    assert seat.overconfidence == pytest.approx(40.0)
    assert seat.is_calibrated is False


def test_well_calibrated_seat_is_flagged_as_such():
    delibs = [_delib(f"t{i}", [("a", "bullish", 50)]) for i in range(4)]
    outcomes = {f"t{i}": _outcome(f"t{i}", 0.05 if i < 2 else -0.05) for i in range(4)}
    seat = score_seats(delibs, outcomes).seats[0]

    assert seat.hit_rate == pytest.approx(0.5)
    assert seat.is_calibrated is True


def test_beats_a_coin_flip_flag():
    delibs = [_delib(f"t{i}", [("a", "bullish", 90)]) for i in range(4)]
    outcomes = {f"t{i}": _outcome(f"t{i}", 0.05) for i in range(4)}
    assert score_seats(delibs, outcomes).seats[0].beats_a_coin_flip is True


def test_worst_calibrated_seat_is_identifiable():
    delibs = [_delib("t1", [("honest", "bullish", 55), ("blowhard", "bullish", 99)])]
    outcomes = {"t1": _outcome("t1", -0.1)}
    card = score_seats(delibs, outcomes)
    assert card.worst_calibrated().seat_id == "blowhard"


# ---------------- shrink fit ----------------

def test_fit_refuses_below_the_sample_floor():
    fit = fit_confidence_shrink([_outcome(f"t{i}", 0.05) for i in range(5)])
    assert not fit.usable
    assert "need 30" in fit.reason
    assert "pessimistic constant stands" in fit.reason


def test_fit_produces_a_shrink_with_enough_samples():
    # 40 theses, all stated at 90 confidence, 70% actually right.
    rows = [
        _outcome(f"t{i}", 0.05, confidence=90.0, correct=(i < 28))
        for i in range(40)
    ]
    fit = fit_confidence_shrink(rows)

    assert fit.usable
    assert fit.samples == 40
    assert fit.realized_hit_rate == pytest.approx(0.7)
    # (0.70 - 0.5) / (0.90 - 0.5) = 0.5
    assert fit.shrink == pytest.approx(0.5, abs=0.01)


def test_overstated_confidence_fits_a_smaller_shrink():
    loud = fit_confidence_shrink([
        _outcome(f"t{i}", 0.05, confidence=95.0, correct=(i < 22)) for i in range(40)
    ])
    honest = fit_confidence_shrink([
        _outcome(f"t{i}", 0.05, confidence=65.0, correct=(i < 26)) for i in range(40)
    ])
    assert loud.shrink < honest.shrink


def test_fit_is_clamped_away_from_face_value():
    perfect = fit_confidence_shrink([
        _outcome(f"t{i}", 0.05, confidence=60.0, correct=True) for i in range(40)
    ])
    assert perfect.shrink <= 0.9


def test_fit_handles_confidence_averaging_fifty():
    rows = [_outcome(f"t{i}", 0.05, confidence=50.0, correct=(i % 2 == 0))
            for i in range(40)]
    fit = fit_confidence_shrink(rows)
    assert not fit.usable
    assert "no signal to scale" in fit.reason


def test_sample_floor_constant_is_sane():
    assert MIN_SAMPLES_FOR_FIT >= 20


# ---------------- persistence ----------------

def test_outcomes_round_trip(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "o.db"))
    store.record_thesis_outcome(
        "t1", "AAPL", realized_return=0.042,
        signal="bullish", confidence=72.0, correct=True, notes="target hit",
    )
    row = store.get_thesis_outcome("t1")

    assert row["realized_return"] == pytest.approx(0.042)
    assert row["correct"] is True
    assert row["notes"] == "target hit"


def test_recording_the_same_thesis_twice_updates(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "o.db"))
    store.record_thesis_outcome("t1", "AAPL", realized_return=0.01, correct=True)
    store.record_thesis_outcome("t1", "AAPL", realized_return=-0.02, correct=False)

    assert len(store.resolved_outcomes()) == 1
    assert store.get_thesis_outcome("t1")["correct"] is False


def test_resolved_outcomes_are_newest_first(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "o.db"))
    for i in range(3):
        store.record_thesis_outcome(f"t{i}", "AAPL", realized_return=0.01)
    ids = [o["thesis_id"] for o in store.resolved_outcomes()]
    assert ids[0] == "t2"


def test_end_to_end_scoring_from_the_store(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "o.db"))
    store.save_deliberation(
        "t1", "AAPL", "equity", "complete",
        _delib("t1", [("risk", "bearish", 80), ("quant", "bullish", 60)])["payload"],
        signal="bullish", confidence=70.0,
    )
    store.record_thesis_outcome("t1", "AAPL", realized_return=-0.08,
                                signal="bullish", confidence=70.0, correct=False)

    card = score_seats(
        store.recent_deliberations(),
        {o["thesis_id"]: o for o in store.resolved_outcomes()},
    )
    by_id = {s.seat_id: s for s in card.seats}
    assert by_id["risk"].hit_rate == 1.0
    assert by_id["quant"].hit_rate == 0.0


# ---------------- dashboard exposure ----------------

def test_scorecard_route_is_empty_before_any_outcomes(tmp_path):
    from fastapi.testclient import TestClient
    from dashboard.runtime import build_runtime
    from dashboard.server import create_app
    from trading.autonomous_loop import WatchedMarket

    class _Stub:
        def post_order(self, o): return {"orderID": "x"}
        def cancel_order(self, i): return {"ok": True}

    rt = build_runtime(
        polymarket_client=_Stub(),
        watched=[WatchedMarket(market_id="m1", token_id="tok-a")],
        memory=MemoryStore(db_path=str(tmp_path / "s.db")),
    )
    body = TestClient(create_app(rt)).get("/api/scorecard").json()

    assert body["resolved"] == 0
    assert body["seats"] == []
    assert body["fit"]["usable"] is False
    assert "need 30" in body["fit"]["reason"]


def test_scorecard_route_reports_seats_once_resolved(tmp_path):
    from fastapi.testclient import TestClient
    from dashboard.runtime import build_runtime
    from dashboard.server import create_app
    from trading.autonomous_loop import WatchedMarket

    class _Stub:
        def post_order(self, o): return {"orderID": "x"}
        def cancel_order(self, i): return {"ok": True}

    store = MemoryStore(db_path=str(tmp_path / "s.db"))
    store.save_deliberation(
        "t1", "AAPL", "equity", "complete",
        _delib("t1", [("risk", "bearish", 80), ("quant", "bullish", 60)])["payload"],
        signal="bullish", confidence=70.0,
    )
    store.record_thesis_outcome("t1", "AAPL", realized_return=-0.08,
                                signal="bullish", confidence=70.0, correct=False)

    rt = build_runtime(
        polymarket_client=_Stub(),
        watched=[WatchedMarket(market_id="m1", token_id="tok-a")],
        memory=store,
    )
    body = TestClient(create_app(rt)).get("/api/scorecard").json()

    assert body["resolved"] == 1
    by_id = {s["seat_id"]: s for s in body["seats"]}
    assert by_id["risk"]["hit_rate"] == 1.0
    assert body["committee"]["hit_rate"] == 0.0
