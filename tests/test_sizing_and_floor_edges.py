"""Two defects that silently select which trades reach the track record.

**The reward:risk floor sat exactly on the default geometry.**
`DEFAULT_TARGET_MULTIPLIER / DEFAULT_STOP_MULTIPLIER` is 3.0/2.0 = 1.5, and
`min_reward_risk_ratio` is 1.5, and the grader tested `r < 1.5`. Because
`r_multiple` is recomputed from `(target - entry)/(entry - stop)` in floating
point, it lands on either side of 1.5 depending on the entry price and the ATR.
Measured over 2000 random (entry, ATR) pairs on the real `build_exit_plan`
geometry: **640 rejected, values spanning 1.4999999999999805 to
1.5000000000000175.**

Roughly a third of otherwise-valid entries were refused with
`rejected_rule="min_reward_risk_ratio"`, which reads as a considered risk
decision. Worse, the rejection is deterministic per price level, so it is a
systematic, price-correlated filter on the sample rather than noise that
averages out. Any 50-trade record built on it is a subsample chosen by IEEE-754.

**The concentration cap was off by one.** `concentration_limit` is passed
`len(holdings)` — the positions open BEFORE this one. With one already open it
received `1` and returned `CONCENTRATION_LIMITS[1] = 1.0`, so position #2 could
be sized at the entire book. Latent only because `max_position_usd = 10`
shadowed it; it goes live the moment that cap is raised.
"""

import random

import pytest

from finance.sizing import CONCENTRATION_FLOOR, CONCENTRATION_LIMITS, concentration_limit
from verification.criteria import VerifiedOutcomeCriteria
from verification.outcome_grader import OutcomeGrader, DirectionalTrade

CRITERIA = VerifiedOutcomeCriteria(max_position_usd=10_000.0)


def _trade(entry: float, atr: float) -> DirectionalTrade:
    """Exactly the geometry `build_exit_plan` produces: 2xATR stop, 3xATR target."""
    return DirectionalTrade(
        symbol="AAPL", side="buy", size_usd=100.0, entry=entry,
        stop=entry - 2.0 * atr, target=entry + 3.0 * atr,
        win_probability=0.6, asset_class="equity", spread_bps=10,
        estimated_slippage_bps=5, session="regular", is_entry=True,
    )


# ---------- the floor must not reject its own default geometry ----------

def test_the_default_geometry_is_never_rejected_by_the_floor():
    """The regression. 2xATR/3xATR IS the reward:risk floor, exactly, and the
    fund's own planner produces nothing else."""
    grader = OutcomeGrader(CRITERIA)
    random.seed(11)
    rejected = []
    for _ in range(2000):
        entry = random.uniform(5, 600)
        atr = entry * random.uniform(0.005, 0.06)
        result = grader.evaluate(_trade(entry, atr))
        if not result.passed and result.rejected_rule == "min_reward_risk_ratio":
            rejected.append((entry, atr))
    assert rejected == [], f"{len(rejected)} of 2000 rejected by float noise"


def test_a_ratio_a_hair_under_the_floor_still_passes():
    """1.4999999999999805 is the floor, in binary. Treating it as a breach is
    an arithmetic artefact, not a risk judgement."""
    grader = OutcomeGrader(CRITERIA)
    trade = DirectionalTrade(
        symbol="AAPL", side="buy", size_usd=100.0, entry=100.0,
        stop=100.0 - 2.0, target=100.0 + 3.0 * (1 - 1e-15),
        win_probability=0.6, asset_class="equity", spread_bps=10,
        estimated_slippage_bps=5, session="regular", is_entry=True,
    )
    assert grader.evaluate(trade).passed


def test_a_genuinely_thin_ratio_is_still_refused():
    """The floor must keep doing its job — this is not a licence to widen it.
    At 1.0R you need to win more than half the time just to pay the spread."""
    grader = OutcomeGrader(CRITERIA)
    thin = DirectionalTrade(
        symbol="AAPL", side="buy", size_usd=100.0, entry=100.0,
        stop=98.0, target=102.0,                      # 1.0R
        win_probability=0.6, asset_class="equity", spread_bps=10,
        estimated_slippage_bps=5, session="regular", is_entry=True,
    )
    result = grader.evaluate(thin)
    assert not result.passed and result.rejected_rule == "min_reward_risk_ratio"


@pytest.mark.parametrize("r_mult, passes", [
    (1.40, False), (1.49, False), (1.50, True), (1.60, True), (2.0, True),
])
def test_the_floor_still_discriminates_away_from_the_knife_edge(r_mult, passes):
    grader = OutcomeGrader(CRITERIA)
    trade = DirectionalTrade(
        symbol="AAPL", side="buy", size_usd=100.0, entry=100.0,
        stop=98.0, target=100.0 + 2.0 * r_mult,
        win_probability=0.6, asset_class="equity", spread_bps=10,
        estimated_slippage_bps=5, session="regular", is_entry=True,
    )
    assert grader.evaluate(trade).passed is passes


# ---------- concentration counts the position being opened ----------

def test_opening_the_second_position_is_not_allowed_the_whole_book():
    """The off-by-one. One position open means this will be the SECOND, so the
    limit that applies is the two-position one."""
    assert concentration_limit(1) == pytest.approx(CONCENTRATION_LIMITS[2])


def test_the_first_position_may_take_the_book():
    """With nothing open there is nothing to diversify against."""
    assert concentration_limit(0) == pytest.approx(CONCENTRATION_LIMITS[1])


@pytest.mark.parametrize("already_open, expected_key", [
    (0, 1), (1, 2), (2, 3), (3, 4),
])
def test_the_limit_reflects_the_book_after_this_position_opens(already_open, expected_key):
    assert concentration_limit(already_open) == pytest.approx(
        CONCENTRATION_LIMITS[expected_key])


def test_five_or_more_positions_hit_the_floor():
    for already_open in (4, 5, 9, 40):
        assert concentration_limit(already_open) == pytest.approx(CONCENTRATION_FLOOR)


def test_the_limit_never_increases_as_the_book_fills():
    limits = [concentration_limit(n) for n in range(0, 8)]
    assert limits == sorted(limits, reverse=True)
