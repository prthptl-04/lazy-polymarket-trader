"""Reward:risk after the cost of getting in and out.

The defect: the fund's exit geometry is fixed at a 2xATR stop and a 3xATR
target, so `r_multiple` is exactly 1.5 for every candidate on every instrument,
forever — and `min_reward_risk_ratio` is also 1.5. A gate that every candidate
meets exactly and none exceeds is not filtering for quality. It cannot
discriminate between two trades because it returns the same number for both.

The variable that DOES differ between candidates is what it costs to trade
them. Netting the round trip out of the reward and into the risk makes the
ratio candidate-specific, so the gate starts doing work — and it does that work
in the right direction, because cost is exactly what separates a tradeable
setup from an identical-looking one on a 185bps book.

This is not loosening the gate. `min_reward_risk_ratio` is unchanged; the trade
now has to clear it after paying to get in and out, which is strictly harder.
"""

import pytest

from verification.outcome_grader import DirectionalTrade


def _trade(**kw):
    base = dict(symbol="X", side="buy", size_usd=100.0, entry=100.0,
                win_probability=0.55, stop=98.0, target=103.0)
    return DirectionalTrade(**{**base, **kw})


def test_a_free_trade_keeps_the_raw_ratio():
    """No spread, nothing to net. 3.0 reward over 2.0 risk is 1.5."""
    t = _trade(spread_bps=0)
    assert t.r_multiple == pytest.approx(1.5)
    assert t.net_r_multiple == pytest.approx(1.5)


def test_a_resting_order_pays_nothing_to_cross():
    """The whole reason weekend crypto is viable: resting at the mark does not
    pay the spread, so a wide book must not be charged as if it did."""
    t = _trade(spread_bps=185, rests=True)
    assert t.net_r_multiple == pytest.approx(1.5)


def test_crossing_a_wide_book_destroys_the_ratio():
    """185bps each way on a 2% stop is not a marginal haircut. The seats used
    to argue about this in prose; now it is a number the gate can refuse."""
    t = _trade(spread_bps=185, rests=False)
    assert t.net_r_multiple is not None
    assert t.net_r_multiple < 0.6, t.net_r_multiple
    assert t.net_r_multiple < t.r_multiple


def test_a_tight_book_is_barely_touched():
    """A 3bps equity spread should not be the difference between trading and
    not trading — if it were, the netting would be miscalibrated."""
    t = _trade(spread_bps=3, rests=False)
    assert 1.45 < t.net_r_multiple < 1.5


def test_slippage_counts_as_cost_too():
    a = _trade(spread_bps=0, estimated_slippage_bps=0)
    b = _trade(spread_bps=0, estimated_slippage_bps=40)
    assert b.net_r_multiple < a.net_r_multiple


def test_the_spread_is_charged_once_per_round_trip_not_twice():
    """Crossing costs half the spread in and half out. Charging two full
    spreads would refuse trades that are actually economic — an error in the
    expensive direction, which is the one that looks like prudence."""
    t = _trade(spread_bps=100, rests=False)
    assert t.round_trip_cost_per_unit == pytest.approx(1.0)


def test_slippage_is_charged_twice_because_there_are_two_fills():
    t = _trade(spread_bps=0, estimated_slippage_bps=50)
    assert t.round_trip_cost_per_unit == pytest.approx(1.0)


def test_a_cost_that_swallows_the_reward_is_refused_not_negative():
    """Reward gone entirely. A negative ratio would sort ABOVE a small positive
    one anywhere the number is compared, so it must be None."""
    t = _trade(spread_bps=900, rests=False)
    assert t.net_r_multiple is None


def test_no_plan_means_no_ratio():
    assert _trade(stop=None).net_r_multiple is None


# ---------------------------------------------------------------- the gate

def _grade(trade):
    from verification.outcome_grader import OutcomeGrader
    return OutcomeGrader().evaluate(trade)


def test_the_gate_refuses_a_cost_the_spread_ceiling_lets_through():
    """Isolates the new rule. 45bps clears the 50bps spread ceiling, so before
    this the trade passed on a raw 1.5 — while the round trip ate more than a
    third of the planned reward.

    Deliberately not tested at 185bps: the spread gate refuses that first, so
    a passing assertion there would be testing gate ordering rather than this
    rule."""
    t = _trade(spread_bps=45, rests=False)
    assert t.net_r_multiple is not None and t.net_r_multiple < 1.5
    result = _grade(t)
    assert not result.passed
    assert result.rejected_rule == "min_net_reward_risk_ratio"


def test_the_same_book_passes_the_ratio_when_the_order_rests():
    """The execution style, not the book width, is what decides. This is the
    finding that made weekend crypto viable, now enforced by the grader rather
    than argued by the seats."""
    result = _grade(_trade(spread_bps=185, rests=True))
    assert "reward:risk" not in (result.reason or "").lower()


def test_the_gross_floor_did_not_move():
    """The net check is ADDITIVE. If the gross floor had been lowered to make
    room for it, this would be a loosening dressed as a correctness fix."""
    from verification.criteria import DEFAULT_CRITERIA
    assert DEFAULT_CRITERIA.min_reward_risk_ratio == 1.5


def test_the_net_floor_leaves_room_for_an_ordinary_fill():
    """The net floor cannot also be 1.5: gross 1.5 is the maximum the fund's
    geometry can produce, so any cost at all would refuse every candidate.
    Measured before this was split: 2000 of 2000 rejected.

    An ordinary name — ATR 2% of price, 10bps spread, 5bps slippage — passes.
    """
    from verification.criteria import DEFAULT_CRITERIA
    assert DEFAULT_CRITERIA.min_net_reward_risk_ratio < DEFAULT_CRITERIA.min_reward_risk_ratio
    ordinary = _trade(entry=100.0, stop=96.0, target=106.0,
                      spread_bps=10, estimated_slippage_bps=5)
    assert _grade(ordinary).passed


def test_a_low_volatility_name_is_refused_on_friction():
    """The gate's whole purpose, and a real behavioural change worth pinning.

    Under ATR-proportional stops a fixed bps cost is a larger share of the risk
    budget the quieter the name. Measured over 2000 random (entry, ATR) pairs at
    10bps + 5bps: every candidate with ATR under 1% of price is refused, about
    half between 1-2%, and none above 2%. That is the gate discriminating on the
    thing that actually varies between candidates.
    """
    quiet = _trade(entry=100.0, stop=99.0, target=101.5,       # ATR 0.5% of price
                   spread_bps=10, estimated_slippage_bps=5)
    assert quiet.r_multiple == pytest.approx(1.5), "same gross ratio as any other"
    assert _grade(quiet).rejected_rule == "min_net_reward_risk_ratio"
