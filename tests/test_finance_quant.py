"""Quant layer: CVaR, Amihud, ATR exits, quality screens, directional sizing.

All implemented from published definitions (Altman 1968, Piotroski 2000,
Amihud 2002, Wilder 1978, Rockafellar & Uryasev 2000) — no code taken from the
AGPL reference repo.

- Acceptance: each formula reproduces a hand-computed value.
- Edge: empty inputs, zero volume, zero denominators, missing history.
- Blind: caps must only ever REDUCE size; a stop must never widen.
"""

import pytest

from finance.exits import (
    Bar,
    average_true_range,
    build_exit_plan,
    is_stop_breached,
    is_target_reached,
    trail_stop,
    true_range,
)
from finance.quality import (
    Financials,
    altman_z_score,
    piotroski_f_score,
)
from finance.risk_metrics import amihud_illiquidity, conditional_value_at_risk, value_at_risk
from finance.sizing import (
    concentration_limit,
    directional_kelly_fraction,
    size_position,
)


# ---------------- CVaR ----------------

def test_cvar_averages_the_tail():
    returns = [-0.10, -0.08, -0.05, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07]
    # alpha=0.2 → worst 2 observations: -0.10 and -0.08 → mean 0.09 loss.
    assert conditional_value_at_risk(returns, alpha=0.2) == pytest.approx(0.09)


def test_cvar_is_at_least_var():
    returns = [-0.2, -0.15, -0.1, -0.05, 0.0, 0.05, 0.1, 0.15, 0.2, 0.25]
    assert conditional_value_at_risk(returns) >= value_at_risk(returns)


def test_cvar_empty_is_zero():
    assert conditional_value_at_risk([]) == 0.0


def test_cvar_all_gains_is_zero_loss():
    assert conditional_value_at_risk([0.01, 0.02, 0.03]) == 0.0


def test_cvar_rejects_bad_alpha():
    with pytest.raises(ValueError):
        conditional_value_at_risk([0.1], alpha=0.0)


def test_cvar_keeps_at_least_one_tail_observation():
    # Tiny sample where alpha*n rounds to 0 — must not divide by zero.
    assert conditional_value_at_risk([-0.5, 0.1], alpha=0.01) == pytest.approx(0.5)


# ---------------- Amihud ----------------

def test_amihud_matches_hand_calculation():
    # |0.02|/1e6 and |0.04|/2e6 both equal 2e-8; scaled by 1e6 → 0.02.
    assert amihud_illiquidity([0.02, 0.04], [1_000_000, 2_000_000]) == pytest.approx(0.02)


def test_amihud_higher_when_less_liquid():
    liquid = amihud_illiquidity([0.01, 0.01], [10_000_000, 10_000_000])
    thin = amihud_illiquidity([0.01, 0.01], [100_000, 100_000])
    assert thin > liquid


def test_amihud_skips_zero_volume_days():
    both = amihud_illiquidity([0.02, 0.05], [1_000_000, 0])
    only_first = amihud_illiquidity([0.02], [1_000_000])
    assert both == pytest.approx(only_first)


def test_amihud_length_mismatch_raises():
    with pytest.raises(ValueError):
        amihud_illiquidity([0.01], [1, 2])


def test_amihud_all_zero_volume_is_zero():
    assert amihud_illiquidity([0.01, 0.02], [0, 0]) == 0.0


# ---------------- ATR ----------------

def test_true_range_uses_the_overnight_gap():
    bar = Bar(high=105, low=102, close=104)
    # Span is 3, but the gap down from 110 is 8.
    assert true_range(bar, previous_close=110) == pytest.approx(8.0)


def test_true_range_without_previous_close_is_the_span():
    assert true_range(Bar(high=105, low=100, close=103), None) == pytest.approx(5.0)


def test_atr_of_constant_ranges_is_that_range():
    bars = [Bar(high=102, low=100, close=101) for _ in range(20)]
    assert average_true_range(bars, period=14) == pytest.approx(2.0)


def test_atr_needs_two_bars():
    assert average_true_range([Bar(high=1, low=0, close=1)]) == 0.0
    assert average_true_range([]) == 0.0


def test_atr_rejects_bad_period():
    with pytest.raises(ValueError):
        average_true_range([Bar(1, 0, 1)], period=0)


# ---------------- exit plans ----------------

@pytest.fixture
def steady_bars():
    return [Bar(high=102, low=100, close=101) for _ in range(20)]


def test_long_plan_places_stop_below_and_target_above(steady_bars):
    plan = build_exit_plan(entry=100.0, bars=steady_bars,
                           stop_multiplier=2.0, target_multiplier=3.0)
    assert plan.stop == pytest.approx(96.0)      # 100 - 2*2
    assert plan.target == pytest.approx(106.0)   # 100 + 3*2
    assert plan.r_multiple == pytest.approx(1.5)


def test_short_plan_mirrors_the_long(steady_bars):
    plan = build_exit_plan(entry=100.0, bars=steady_bars, direction="short",
                           stop_multiplier=2.0, target_multiplier=3.0)
    assert plan.stop == pytest.approx(104.0)
    assert plan.target == pytest.approx(94.0)


def test_plan_refuses_without_volatility():
    with pytest.raises(ValueError, match="ATR"):
        build_exit_plan(entry=100.0, bars=[Bar(high=1, low=1, close=1)])


def test_plan_refuses_when_stop_would_go_negative():
    wild = [Bar(high=200, low=1, close=100) for _ in range(20)]
    with pytest.raises(ValueError, match="too volatile"):
        build_exit_plan(entry=10.0, bars=wild)


def test_stop_distance_pct(steady_bars):
    plan = build_exit_plan(entry=100.0, bars=steady_bars, stop_multiplier=2.0)
    assert plan.stop_distance_pct == pytest.approx(0.04)


def test_breach_and_target_detection(steady_bars):
    plan = build_exit_plan(entry=100.0, bars=steady_bars)
    assert is_stop_breached(plan, 95.0)
    assert not is_stop_breached(plan, 99.0)
    assert is_target_reached(plan, 107.0)
    assert not is_target_reached(plan, 101.0)


def test_short_breach_direction(steady_bars):
    plan = build_exit_plan(entry=100.0, bars=steady_bars, direction="short")
    assert is_stop_breached(plan, 105.0)
    assert is_target_reached(plan, 93.0)


# ---------------- trailing ----------------

def test_trail_ratchets_stop_up_on_a_long(steady_bars):
    plan = build_exit_plan(entry=100.0, bars=steady_bars, stop_multiplier=2.0)
    trailed = trail_stop(plan, price=110.0)
    assert trailed.stop == pytest.approx(106.0)


def test_trail_never_loosens_a_stop(steady_bars):
    """A stop that can widen is not a stop."""
    plan = build_exit_plan(entry=100.0, bars=steady_bars, stop_multiplier=2.0)
    trailed = trail_stop(plan, price=90.0)      # price fell
    assert trailed.stop == pytest.approx(plan.stop)


def test_trail_never_loosens_a_short_stop(steady_bars):
    plan = build_exit_plan(entry=100.0, bars=steady_bars, direction="short")
    trailed = trail_stop(plan, price=120.0)
    assert trailed.stop == pytest.approx(plan.stop)


# ---------------- Altman ----------------

def _healthy() -> Financials:
    return Financials(
        total_assets=1000, total_liabilities=300,
        current_assets=500, current_liabilities=200,
        retained_earnings=400, ebit=150, revenue=1200, market_cap=2000,
    )


def test_altman_healthy_company_is_safe():
    r = altman_z_score(_healthy())
    assert r.zone == "safe"
    assert not r.is_distressed
    assert r.score > 2.99


def test_altman_matches_hand_calculation():
    r = altman_z_score(_healthy())
    # 1.2(.3) + 1.4(.4) + 3.3(.15) + 0.6(6.667) + 1.0(1.2)
    expected = 1.2 * 0.3 + 1.4 * 0.4 + 3.3 * 0.15 + 0.6 * (2000 / 300) + 1.2
    assert r.score == pytest.approx(expected, rel=1e-3)


def test_altman_distressed_company():
    f = Financials(
        total_assets=1000, total_liabilities=1200,
        current_assets=100, current_liabilities=400,
        retained_earnings=-500, ebit=-100, revenue=200, market_cap=50,
    )
    r = altman_z_score(f)
    assert r.zone == "distress" and r.is_distressed


def test_altman_needs_positive_assets():
    r = altman_z_score(Financials(total_assets=0, total_liabilities=10))
    assert r.score is None and r.zone == "unknown"


def test_altman_needs_liabilities_for_the_equity_cushion():
    r = altman_z_score(Financials(total_assets=100, total_liabilities=0))
    assert r.zone == "unknown"


# ---------------- Piotroski ----------------

def test_piotroski_perfect_nine():
    prior = Financials(
        total_assets=1000, total_liabilities=400, net_income=50,
        operating_cash_flow=60, long_term_debt=300,
        current_assets=300, current_liabilities=200,
        shares_outstanding=100, revenue=800, gross_profit=240,
    )
    current = Financials(
        total_assets=1000, total_liabilities=350, net_income=100,
        operating_cash_flow=150, long_term_debt=200,
        current_assets=400, current_liabilities=150,
        shares_outstanding=100, revenue=900, gross_profit=315,
    )
    r = piotroski_f_score(current, prior)
    assert r.score == 9
    assert r.is_strong and not r.is_weak


def test_piotroski_penalises_dilution():
    prior = Financials(total_assets=1000, total_liabilities=400, shares_outstanding=100)
    current = Financials(total_assets=1000, total_liabilities=400, shares_outstanding=150)
    r = piotroski_f_score(current, prior)
    assert r.signals["no_dilution"] is False


def test_piotroski_equal_share_count_passes():
    prior = Financials(total_assets=1000, total_liabilities=400, shares_outstanding=100)
    current = Financials(total_assets=1000, total_liabilities=400, shares_outstanding=100)
    assert piotroski_f_score(current, prior).signals["no_dilution"] is True


def test_piotroski_accruals_flag_catches_uncashed_earnings():
    prior = Financials(total_assets=1000, total_liabilities=400)
    current = Financials(total_assets=1000, total_liabilities=400,
                         net_income=100, operating_cash_flow=20)
    r = piotroski_f_score(current, prior)
    assert r.signals["accruals_quality"] is False


def test_piotroski_weak_company():
    prior = Financials(total_assets=1000, total_liabilities=400, net_income=100,
                       operating_cash_flow=120, long_term_debt=100,
                       shares_outstanding=100, revenue=900, gross_profit=300,
                       current_assets=400, current_liabilities=100)
    current = Financials(total_assets=1000, total_liabilities=700, net_income=-50,
                         operating_cash_flow=-20, long_term_debt=400,
                         shares_outstanding=200, revenue=700, gross_profit=140,
                         current_assets=200, current_liabilities=300)
    r = piotroski_f_score(current, prior)
    assert r.is_weak


def test_piotroski_needs_both_periods():
    f = Financials(total_assets=0, total_liabilities=0)
    assert piotroski_f_score(f, f).score is None


def test_piotroski_missing_revenue_does_not_award_a_point():
    prior = Financials(total_assets=1000, total_liabilities=400, revenue=0, gross_profit=0)
    current = Financials(total_assets=1000, total_liabilities=400, revenue=0, gross_profit=0)
    r = piotroski_f_score(current, prior)
    assert r.signals["improving_gross_margin"] is False


# ---------------- directional Kelly + caps ----------------

def test_kelly_fraction_matches_formula():
    # p=0.6, b=2 → 0.6 - 0.4/2 = 0.4
    assert directional_kelly_fraction(0.6, 2.0) == pytest.approx(0.4)


def test_kelly_negative_without_edge():
    assert directional_kelly_fraction(0.3, 1.0) < 0


def test_kelly_rejects_impossible_probability():
    with pytest.raises(ValueError):
        directional_kelly_fraction(1.0, 2.0)


@pytest.mark.parametrize("already_open,expected", [
    # The argument is what is held BEFORE this position opens, so the limit
    # that applies is the one for the book AFTER it does. This test used to
    # read the dict straight through, which is why the second position could
    # be sized at 100% of the book.
    (0, 1.0), (1, 0.5), (2, 0.34), (3, 0.25), (4, 0.20), (11, 0.20),
])
def test_concentration_limits(already_open, expected):
    assert concentration_limit(already_open) == expected


@pytest.fixture
def plan():
    bars = [Bar(high=102, low=100, close=101) for _ in range(20)]
    return build_exit_plan(entry=100.0, bars=bars,
                           stop_multiplier=2.0, target_multiplier=3.0)


def test_no_edge_produces_no_position(plan):
    r = size_position(win_probability=0.3, plan=plan, bankroll_usd=10_000)
    assert not r.is_actionable
    assert "no edge" in r.reason


def test_risk_budget_caps_the_loss(plan):
    r = size_position(win_probability=0.8, plan=plan, bankroll_usd=10_000,
                      risk_budget=0.02)
    # Stop is 4% away; 2% budget → at most $5,000 exposure → $200 at risk.
    assert r.risk_usd <= 10_000 * 0.02 + 1e-6
    assert r.binding_constraint in ("risk_budget", "kelly", "concentration")


def test_concentration_binds_with_many_positions(plan):
    r = size_position(win_probability=0.95, plan=plan, bankroll_usd=10_000,
                      open_positions=5, risk_budget=0.5)
    assert r.size_usd <= 10_000 * 0.20 + 1e-6


def test_cash_cap_binds(plan):
    r = size_position(win_probability=0.9, plan=plan, bankroll_usd=10_000,
                      available_cash_usd=100.0, risk_budget=0.5)
    assert r.size_usd == pytest.approx(100.0)
    assert r.binding_constraint == "cash"


def test_max_position_cap_binds(plan):
    r = size_position(win_probability=0.9, plan=plan, bankroll_usd=10_000,
                      max_position_usd=250.0, risk_budget=0.5)
    assert r.size_usd == pytest.approx(250.0)


def test_cvar_tightens_size_beyond_the_stop(plan):
    """A gap through the stop is the loss the stop can't prevent."""
    without = size_position(win_probability=0.8, plan=plan, bankroll_usd=10_000)
    with_tail = size_position(win_probability=0.8, plan=plan, bankroll_usd=10_000,
                              cvar=0.20)
    assert with_tail.size_usd < without.size_usd
    assert with_tail.binding_constraint == "risk_budget_cvar"


def test_cvar_below_stop_distance_does_not_loosen(plan):
    base = size_position(win_probability=0.8, plan=plan, bankroll_usd=10_000)
    tiny = size_position(win_probability=0.8, plan=plan, bankroll_usd=10_000, cvar=0.001)
    assert tiny.size_usd <= base.size_usd + 1e-6


def test_caps_only_ever_reduce(plan):
    """Every constraint is a min(). Adding one must never grow the position."""
    unconstrained = size_position(win_probability=0.75, plan=plan,
                                  bankroll_usd=10_000, risk_budget=0.99)
    constrained = size_position(win_probability=0.75, plan=plan,
                                bankroll_usd=10_000, risk_budget=0.99,
                                open_positions=5, available_cash_usd=500.0,
                                max_position_usd=400.0)
    assert constrained.size_usd <= unconstrained.size_usd


def test_quantity_matches_size_over_entry(plan):
    r = size_position(win_probability=0.7, plan=plan, bankroll_usd=10_000)
    assert r.quantity == pytest.approx(r.size_usd / plan.entry)


def test_zero_bankroll_is_refused(plan):
    assert not size_position(win_probability=0.9, plan=plan, bankroll_usd=0).is_actionable


def test_invalid_multiplier_raises(plan):
    with pytest.raises(ValueError):
        size_position(win_probability=0.7, plan=plan, bankroll_usd=1000,
                      kelly_multiplier=1.5)


def test_half_kelly_is_half_of_full(plan):
    """`open_positions=0` so the CONCENTRATION cap cannot bind.

    It used to pass 1, which returned a 100% limit under the old off-by-one.
    Now one position already open means this is the second, capped at 50% —
    which binds on full Kelly at p=0.8 and not on half, so the two stop being
    proportional. That is the cap working; this test is about the multiplier,
    so it isolates it.
    """
    half = size_position(win_probability=0.8, plan=plan, bankroll_usd=10_000,
                         kelly_multiplier=0.5, risk_budget=0.99, open_positions=0)
    full = size_position(win_probability=0.8, plan=plan, bankroll_usd=10_000,
                         kelly_multiplier=1.0, risk_budget=0.99, open_positions=0)
    assert half.size_usd == pytest.approx(full.size_usd / 2)
