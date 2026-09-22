"""Every deliberation is scored, whether or not it became a trade.

The operator's question, and it is the right one: if the committee stands aside
33 times out of 36, what is the point of the calibration machinery? Seat
weights, the confidence shrink and the post-mortem lessons are all gated behind
30 resolved outcomes, and outcomes only existed when a position closed. A
committee that declines produces nothing to learn from, so it never improves,
so it declines again.

The premise was wrong. A deliberation is a PREDICTION — a named instrument, a
direction, a confidence, at a known price — and the market resolves it whether
or not we took the position. NEAR-USD moved regardless of whether we bought it.
Thirty-six debates were thirty-six scoreable predictions sitting unused.

So the seats are now scored on every call they make. The bootstrap problem
dissolves: calibration starts from debate one, and a seat that is consistently
wrong loses weight without the fund having to lose money to find out.

TWO BOUNDARIES THIS MUST NOT CROSS.

**It cannot unlock live trading.** Rule #13 wants 50 paper TRADES, and the live
gate counts closed round trips, not predictions. A shadow outcome is not a
trade and must never be mistaken for one.

**It must not fit the confidence shrink.** The shrink maps stated confidence to
a win probability for POSITIONS, and a position is bounded by its stop — it can
be taken out by a move that later reverses, while a 24h price change never is.
Predictions therefore look better than the trades they would have become, and a
shrink fitted on them would be optimistic. Optimism sizes bigger, which is the
expensive direction to be wrong in.
"""

import time

import pytest

from memory.store import MemoryStore
from roundtable.shadow import (
    SHADOW_NOTE, ShadowResolver, is_shadow, shadow_return,
)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "s.db"))


def _debate(store, tid, symbol="NEAR-USD", signal="bullish", price=3.20,
            age_hours=30.0, status="complete"):
    store.save_deliberation(
        tid, symbol, "crypto", status,
        {"opinions": [{"seat_id": "quant", "seat_name": "Quant",
                       "signal": signal, "confidence": 60.0, "failed": False}],
         "consensus": {"signal": signal, "confidence": 60.0},
         "tally": {signal: 1}, "price": price},
        signal=signal, confidence=60.0)
    store._conn.execute("UPDATE deliberations SET created = ? WHERE thesis_id = ?",
                        (time.time() - age_hours * 3600, tid))
    store._conn.commit()


# ---------------------------------------------------------------- the maths

def test_a_long_call_is_scored_on_the_move_in_its_favour():
    assert shadow_return("bullish", entry=100.0, later=110.0) == pytest.approx(0.10)


def test_a_short_call_is_scored_by_direction_not_by_sign():
    """A correct bearish call shows a negative price move. Scoring on the raw
    sign would mark every right short as a loss."""
    assert shadow_return("bearish", entry=100.0, later=90.0) == pytest.approx(0.10)


def test_a_neutral_call_has_no_directional_return():
    """It made no directional claim, so there is nothing to be right about.
    Scoring it as wrong whenever price moved would punish correct caution."""
    assert shadow_return("neutral", entry=100.0, later=150.0) is None


def test_a_missing_price_yields_nothing_rather_than_zero():
    """Zero is a real result — a flat market. Unknown is not."""
    assert shadow_return("bullish", entry=0.0, later=10.0) is None
    assert shadow_return("bullish", entry=100.0, later=None) is None


# ---------------------------------------------------------------- resolving

def _resolve(store, prices, **kw):
    r = ShadowResolver(memory=store, quote=lambda s: prices.get(s), **kw)
    return r.resolve_due()


def test_a_debate_older_than_the_horizon_is_scored(store):
    _debate(store, "t1", age_hours=30)
    assert _resolve(store, {"NEAR-USD": 3.52}) == 1
    out = store.resolved_outcomes(limit=5)[0]
    assert out["thesis_id"] == "t1"
    assert out["realized_return"] == pytest.approx(0.10)


def test_a_debate_inside_the_horizon_is_left_alone(store):
    """Scoring a one-hour-old call against a 24h thesis measures noise."""
    _debate(store, "t1", age_hours=2)
    assert _resolve(store, {"NEAR-USD": 3.52}) == 0


def test_an_unfinished_debate_is_never_scored(store):
    _debate(store, "t1", status="in_progress")
    assert _resolve(store, {"NEAR-USD": 3.52}) == 0


def test_a_debate_is_scored_once(store):
    _debate(store, "t1")
    assert _resolve(store, {"NEAR-USD": 3.52}) == 1
    assert _resolve(store, {"NEAR-USD": 9.99}) == 0, "rescoring would fake a sample"


def test_a_neutral_debate_is_recorded_but_carries_no_direction(store):
    """Kept, because the operator should see how often the table stood aside
    while the market moved — but with `correct` unset, so it cannot flatter or
    damage a hit rate it never participated in."""
    _debate(store, "t1", signal="neutral")
    assert _resolve(store, {"NEAR-USD": 4.00}) == 1
    assert store.resolved_outcomes(limit=1)[0]["correct"] is None


def test_an_unquotable_symbol_is_left_for_next_time(store):
    """A delisted pair or a provider blip must not burn the sample."""
    _debate(store, "t1")
    assert _resolve(store, {}) == 0
    assert _resolve(store, {"NEAR-USD": 3.52}) == 1


def test_a_broken_quote_source_resolves_nothing_rather_than_raising(store):
    _debate(store, "t1")
    r = ShadowResolver(memory=store,
                       quote=lambda s: (_ for _ in ()).throw(RuntimeError("down")))
    assert r.resolve_due() == 0


# ---------------------------------------------------------------- boundaries

def test_a_shadow_outcome_is_marked_as_one(store):
    _debate(store, "t1")
    _resolve(store, {"NEAR-USD": 3.52})
    assert is_shadow(store.resolved_outcomes(limit=1)[0])


def test_shadow_outcomes_never_count_toward_the_live_gate(store):
    """Rule #13 wants 50 paper TRADES. The gate counts closed round trips, and
    a prediction is not a trade — this pins that they cannot be confused."""
    from trading.live_gate import LiveTradingGate
    for i in range(60):
        _debate(store, f"t{i}")
    _resolve(store, {"NEAR-USD": 3.52})
    gate = LiveTradingGate(memory=store, bankroll_usd=500.0)
    assert gate.status()["graded_paper_trades"] == 0


def test_shadow_outcomes_are_excluded_from_the_confidence_shrink(store):
    """The shrink sizes real positions. A position is bounded by its stop and
    can be taken out by a move that later reverses; a 24h price change never
    is. Fitting on predictions would be optimistic, and optimism sizes bigger."""
    from roundtable.calibration import fit_confidence_shrink
    for i in range(40):
        store.record_thesis_outcome(f"s{i}", "NEAR-USD", 0.10, signal="bullish",
                                    confidence=60.0, correct=True, notes=SHADOW_NOTE)
    fit = fit_confidence_shrink(store.resolved_outcomes(limit=100))
    assert not fit.usable, "predictions must not fit a sizing parameter"


def test_seat_scoring_DOES_use_them(store):
    """The whole point. A seat is scored on whether its direction was right,
    which needs no position at all."""
    from roundtable.calibration import score_seats
    for i in range(40):
        _debate(store, f"t{i}")
    _resolve(store, {"NEAR-USD": 3.52})
    card = score_seats(store.recent_deliberations(limit=100),
                       {o["thesis_id"]: o for o in store.resolved_outcomes(limit=100)})
    quant = next(s for s in card.seats if s.seat_id == "quant")
    assert quant.samples >= 30 and quant.hit_rate == pytest.approx(1.0)


def test_a_historical_debate_is_recovered_from_its_evidence_block(store):
    """Deliberations written before `price` became a payload field still carry
    the rendered evidence. Discarding them would throw away real calls the fund
    has already paid for — 34 of them when this was written, above the bar that
    switches calibration on."""
    store.save_deliberation(
        "old", "NEAR-USD", "crypto", "complete",
        {"opinions": [{"seat_id": "quant", "seat_name": "Quant",
                       "signal": "bullish", "confidence": 60.0, "failed": False}],
         "consensus": {"signal": "bullish", "confidence": 60.0},
         "evidence": "INSTRUMENT: NEAR-USD (crypto)\nSession: crypto_only\n"
                     "Last price: 3.20\nSpread: 40 bps"},
        signal="bullish", confidence=60.0)
    store._conn.execute("UPDATE deliberations SET created = ? WHERE thesis_id = 'old'",
                        (time.time() - 30 * 3600,))
    store._conn.commit()

    assert _resolve(store, {"NEAR-USD": 3.52}) == 1
    assert store.resolved_outcomes(limit=1)[0]["realized_return"] == pytest.approx(0.10)


def test_the_stored_field_wins_over_the_parsed_one(store):
    """Parsing rendered text is the fallback, never the primary path."""
    _debate(store, "t1", price=100.0)
    store._conn.execute(
        "UPDATE deliberations SET payload = json_set(payload, '$.evidence', "
        "'Last price: 999.0') WHERE thesis_id = 't1'")
    store._conn.commit()
    _resolve(store, {"NEAR-USD": 110.0})
    assert store.resolved_outcomes(limit=1)[0]["realized_return"] == pytest.approx(0.10)
