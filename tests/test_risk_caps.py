"""The risk caps, and the relationships between them.

Changed 2026-09-20 at the account owner's direction: `max_position_usd` 10 ->
150 against the funded $500 bankroll, choosing maximum Kelly expression.

The old value was correct for the $100 smoke-test book it was written for and
became wrong at funding. `FundLoop.run_cycle` sets
`pipeline.bankroll_usd = equity_usd`, so Kelly already sized off the live $500
while the cap held every position at $10 — the cap had stopped being a cap and
become the size, at every chair confidence above ~35.

At 150 the per-position cap no longer binds: half-Kelly under the fixed 1.5
payoff ratio maxes at $145.83 at confidence 100. **Concentration is now the
real constraint**, which is only true because the off-by-one in
`concentration_limit` was fixed first — before that, position #2 could take the
whole book and nothing would have stopped it.

These tests pin the relationships, not the numbers for their own sake: a cap
that exceeds the bankroll is not a cap, and a kill-switch that cannot be
reached is decoration.
"""

import pytest

from finance.sizing import CONCENTRATION_FLOOR, concentration_limit
from trading.fund_config import load_config
from verification.criteria import DEFAULT_CRITERIA

BANKROLL = 500.0


def test_the_position_cap_is_a_fraction_of_the_bankroll_not_a_multiple():
    """A cap larger than the book is not a cap. `LiveTradingGate._caps_ok`
    refuses live trading on exactly this condition."""
    assert 0 < DEFAULT_CRITERIA.max_position_usd <= BANKROLL


def test_the_cap_lets_half_kelly_express_itself():
    """The point of the change. Half-Kelly's maximum under the fund's fixed
    2xATR/3xATR geometry is bankroll x 0.5 x ((5/3)(0.75) - 2/3) = $145.83 at
    chair confidence 100, so a cap at or above that never binds."""
    payoff = 3.0 / 2.0
    p_at_full_confidence = 0.5 + (1.0 - 0.5) * 0.5       # confidence 100, shrink 0.5
    f_star = p_at_full_confidence - (1 - p_at_full_confidence) / payoff
    max_half_kelly = BANKROLL * f_star * 0.5
    assert max_half_kelly == pytest.approx(145.83, abs=0.01)
    assert DEFAULT_CRITERIA.max_position_usd >= max_half_kelly


def test_concentration_is_what_actually_limits_a_filling_book():
    """With the per-position cap non-binding, diversification is the live
    constraint. It must bite before the book is fully committed to few names."""
    cap = DEFAULT_CRITERIA.max_position_usd
    # Positions #1-#3 are bounded by the cap (34% of 500 = $170 > $150), and
    # concentration takes over from the FOURTH name onward. Both are live: the
    # cap stops one name from being outsized early, concentration stops the
    # book from concentrating as it fills.
    assert BANKROLL * concentration_limit(2) > cap        # #3: 170 -> cap binds
    assert BANKROLL * concentration_limit(3) < cap        # #4: 125 -> concentration binds
    assert BANKROLL * concentration_limit(4) < cap        # #5: 100 -> concentration binds


def test_a_full_book_is_still_diversified():
    """Five or more names each capped at the floor, so no single position can
    dominate a fully-deployed book."""
    assert concentration_limit(4) == pytest.approx(CONCENTRATION_FLOOR)
    assert BANKROLL * CONCENTRATION_FLOOR == pytest.approx(100.0)


def test_the_daily_loss_limit_is_reachable_but_not_trivial():
    """A kill-switch that trips on two trades is a nuisance; one that can never
    trip is decoration. At the 15% maximum stop distance, a worst-case
    stop-out costs 0.15 x position."""
    cfg = load_config()
    worst_stop_out = DEFAULT_CRITERIA.max_position_usd * DEFAULT_CRITERIA.max_stop_distance_pct
    trips_after = cfg.max_daily_loss_usd / worst_stop_out
    assert 2 < trips_after < 10, f"{trips_after:.1f} worst-case stop-outs to trip"


def test_the_daily_loss_limit_has_exactly_one_home():
    """It lives in config/fund.toml and reaches DailyLossKillSwitch through
    FundConfig. `verification.criteria` deliberately does NOT carry one: a
    second copy had zero code readers and existed only to drift out of step
    with the one that is enforced."""
    assert not hasattr(DEFAULT_CRITERIA, "max_daily_loss_usd")
    assert load_config().max_daily_loss_usd > 0


def test_the_daily_loss_limit_is_a_sane_fraction_of_the_bankroll():
    cfg = load_config()
    assert 0 < cfg.max_daily_loss_usd <= cfg.bankroll_usd * 0.25
