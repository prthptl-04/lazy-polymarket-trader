"""Directional grading — the gate for equity and crypto positions.

CLAUDE.md #3: every trade is graded. Before this, nothing directional could be
graded at all, because ProposedTrade requires price to be a probability.

- Acceptance: a sane swing entry passes.
- Blind (the ones that matter): an entry with no stop is refused, a stop on the
  wrong side of entry is refused, and extended-hours trades face tighter bars.
- Edge: closing orders skip entry-only rules; dispatch preserves the old path.
"""

import pytest

from verification.criteria import VerifiedOutcomeCriteria
from verification.outcome_grader import (
    DirectionalTrade,
    OutcomeGrader,
    ProposedTrade,
)


# Roomy criteria so each test isolates the one rule it targets.
CRITERIA = VerifiedOutcomeCriteria(
    max_position_usd=10_000.0,
    max_slippage_bps=50,
    min_expected_edge_bps=20,
)


@pytest.fixture
def grader():
    return OutcomeGrader(CRITERIA)


def _entry(**kw) -> DirectionalTrade:
    """A reasonable long: 4% stop, 8% target, 2R, 60% hit rate."""
    base = dict(
        symbol="AAPL", side="buy", size_usd=1_000.0, entry=100.0,
        stop=96.0, target=108.0, win_probability=0.6,
        spread_bps=10, estimated_slippage_bps=5, session="regular",
    )
    base.update(kw)
    return DirectionalTrade(**base)


# ---------------- acceptance ----------------

def test_sane_long_entry_passes(grader):
    r = grader.evaluate(_entry())
    assert r.passed, r.reason


def test_sane_short_entry_passes(grader):
    r = grader.evaluate(_entry(side="sell", stop=104.0, target=92.0))
    assert r.passed, r.reason


def test_crypto_entry_passes(grader):
    r = grader.evaluate(_entry(symbol="BTC", asset_class="crypto", session="crypto_only"))
    assert r.passed, r.reason


# ---------------- the stop is mandatory ----------------

def test_entry_without_a_stop_is_refused(grader):
    r = grader.evaluate(_entry(stop=None))
    assert not r.passed
    assert r.rejected_rule == "require_stop_loss"
    assert "unbounded" in r.reason


def test_long_stop_above_entry_is_refused(grader):
    r = grader.evaluate(_entry(stop=105.0))
    assert not r.passed and r.rejected_rule == "stop_side"


def test_short_stop_below_entry_is_refused(grader):
    r = grader.evaluate(_entry(side="sell", stop=95.0, target=90.0))
    assert not r.passed and r.rejected_rule == "stop_side"


def test_stop_equal_to_entry_is_refused(grader):
    r = grader.evaluate(_entry(stop=100.0))
    assert not r.passed and r.rejected_rule == "stop_side"


def test_absurdly_wide_stop_is_refused(grader):
    # 50% away — "a hope, not a stop".
    r = grader.evaluate(_entry(stop=50.0, target=200.0))
    assert not r.passed and r.rejected_rule == "max_stop_distance_pct"


def test_stop_loss_can_be_disabled_only_by_changing_criteria(grader):
    lenient = OutcomeGrader(VerifiedOutcomeCriteria(
        max_position_usd=10_000.0, require_stop_loss=False,
    ))
    # Still refused — no stop means no target check either, but the target
    # rule catches it. The point: turning the flag off is not a free pass.
    r = lenient.evaluate(_entry(stop=None, target=None))
    assert not r.passed


# ---------------- target and reward:risk ----------------

def test_entry_without_a_target_is_refused(grader):
    r = grader.evaluate(_entry(target=None))
    assert not r.passed and r.rejected_rule == "require_target"


def test_long_target_below_entry_is_refused(grader):
    r = grader.evaluate(_entry(target=95.0))
    assert not r.passed and r.rejected_rule == "target_side"


def test_short_target_above_entry_is_refused(grader):
    r = grader.evaluate(_entry(side="sell", stop=104.0, target=110.0))
    assert not r.passed and r.rejected_rule == "target_side"


def test_poor_reward_risk_is_refused(grader):
    # 4% stop, 4% target → 1.0R, below the 1.5 floor.
    r = grader.evaluate(_entry(target=104.0))
    assert not r.passed and r.rejected_rule == "min_reward_risk_ratio"


def test_reward_risk_exactly_at_the_floor_passes(grader):
    # 4% stop, 6% target → exactly 1.5R.
    r = grader.evaluate(_entry(target=106.0, win_probability=0.7))
    assert r.passed, r.reason


# ---------------- expected edge ----------------

def test_negative_expected_value_is_refused(grader):
    # 2R but only a 20% hit rate → EV is negative.
    r = grader.evaluate(_entry(win_probability=0.2))
    assert not r.passed and r.rejected_rule == "min_expected_edge_bps"


def test_edge_formula_matches_hand_calculation():
    t = _entry()   # 4% risk, 8% reward, p=0.6
    # 0.6*0.08 - 0.4*0.04 = 0.048 - 0.016 = 0.032 → 320 bps
    assert t.expected_edge_bps == 320


def test_r_multiple_and_distances():
    t = _entry()
    assert t.r_multiple == pytest.approx(2.0)
    assert t.stop_distance_pct == pytest.approx(0.04)


def test_impossible_probability_is_refused(grader):
    assert not grader.evaluate(_entry(win_probability=1.0)).passed
    assert not grader.evaluate(_entry(win_probability=0.0)).passed


# ---------------- liquidity and costs ----------------

def test_wide_spread_is_refused_in_regular_session(grader):
    r = grader.evaluate(_entry(spread_bps=200))
    assert not r.passed and r.rejected_rule == "max_spread_bps"


def test_missing_spread_skips_the_liquidity_check(grader):
    assert grader.evaluate(_entry(spread_bps=None)).passed


def test_excess_slippage_is_refused(grader):
    r = grader.evaluate(_entry(estimated_slippage_bps=500))
    assert not r.passed and r.rejected_rule == "max_slippage_bps"


# ---------------- extended hours are stricter ----------------

def test_extended_hours_allows_a_wider_spread(grader):
    # 80bps fails the 50bps regular limit but clears the 100bps extended one.
    assert not grader.evaluate(_entry(spread_bps=80)).passed
    assert grader.evaluate(
        _entry(spread_bps=80, session="premarket", win_probability=0.7)
    ).passed


def test_extended_hours_demands_a_better_edge(grader):
    # EV here is 40bps: clears the 20bps regular bar, fails the 40... use a
    # thinner edge to sit between the two thresholds.
    thin = _entry(win_probability=0.42, target=108.0, stop=96.0)
    # 0.42*0.08 - 0.58*0.04 = 0.0336 - 0.0232 = 0.0104 → 104bps
    assert thin.expected_edge_bps == 104

    strict = OutcomeGrader(VerifiedOutcomeCriteria(
        max_position_usd=10_000.0, min_expected_edge_bps=100,
        extended_hours_edge_multiplier=2.0,
    ))
    assert strict.evaluate(thin).passed                       # 104 >= 100
    assert not strict.evaluate(
        _entry(win_probability=0.42, session="premarket")
    ).passed                                                   # 104 < 200


def test_after_hours_is_treated_as_extended(grader):
    r = grader.evaluate(_entry(spread_bps=80, session="after_hours", win_probability=0.7))
    assert r.passed


# ---------------- closing orders ----------------

def test_closing_order_needs_no_stop_or_target(grader):
    r = grader.evaluate(_entry(side="sell", stop=None, target=None, is_entry=False))
    assert r.passed
    assert "closing order" in r.reason


def test_closing_order_still_faces_size_and_spread_rules(grader):
    too_big = grader.evaluate(
        _entry(side="sell", stop=None, target=None, is_entry=False, size_usd=99_999.0)
    )
    assert not too_big.passed and too_big.rejected_rule == "max_position_usd"

    too_wide = grader.evaluate(
        _entry(side="sell", stop=None, target=None, is_entry=False, spread_bps=900)
    )
    assert not too_wide.passed and too_wide.rejected_rule == "max_spread_bps"


# ---------------- basic validation ----------------

def test_bad_side_is_refused(grader):
    r = grader.evaluate(_entry(side="YES"))
    assert not r.passed and r.rejected_rule == "side"


def test_non_positive_entry_is_refused(grader):
    r = grader.evaluate(_entry(entry=0.0))
    assert not r.passed and r.rejected_rule == "entry_price"


def test_non_positive_size_is_refused(grader):
    r = grader.evaluate(_entry(size_usd=0.0))
    assert not r.passed and r.rejected_rule == "size_positive"


def test_oversized_position_is_refused(grader):
    r = grader.evaluate(_entry(size_usd=50_000.0))
    assert not r.passed and r.rejected_rule == "max_position_usd"


# ---------------- dispatch ----------------

def test_prediction_trades_still_route_to_the_old_path(grader):
    pred = ProposedTrade(
        market_id="m1", side="YES", size_usd=5.0, price=0.4,
        orderbook_depth_usd=10_000.0, expected_edge_bps=300,
        estimated_slippage_bps=10,
    )
    assert grader.evaluate(pred).passed


def test_prediction_rules_are_unchanged_by_the_new_path(grader):
    bad_price = ProposedTrade(
        market_id="m1", side="YES", size_usd=5.0, price=1.5,
        orderbook_depth_usd=10_000.0, expected_edge_bps=300,
        estimated_slippage_bps=10,
    )
    r = grader.evaluate(bad_price)
    assert not r.passed and r.rejected_rule == "price_range"


def test_directional_trade_is_not_graded_by_probability_rules(grader):
    """A $231 entry must not be rejected for being 'outside (0, 1)'."""
    r = grader.evaluate(_entry(entry=231.40, stop=222.0, target=250.0))
    assert r.passed, r.reason
