"""A resting limit priced to be reachable, bounded by what the grader allows.

Measured: four orders placed, zero filled. The limit sat at the MARK, and on a
market-maker-routed venue the ask is permanently ~92bps above the mark — so a
resting buy only filled if the whole quote fell to it. It never did.

Crossing is not the alternative and the arithmetic says so: the net
reward:risk floor tolerates a round trip of `0.128 x ATR`, and a 185bps
crossing needs ATR >= 14.5% of price to clear it. Typical crypto ATR is 2-6%.
Crossing can never clear the floor.

So the limit is improved toward the touch by EXACTLY as much as the floor
allows and no more. The grader's cost budget sets the price:

    improvement = min(0.128 x ATR x safety, half-spread)

That makes the required move smaller without making the trade uneconomic, and
it is self-limiting: a name whose ATR cannot pay for any improvement gets none,
which is the correct answer for a name too quiet to trade through this spread.

It is an improvement, not a cure. A passive order on a 185bps retail book still
needs the market to come some of the way.
"""

import pytest

from trading.pipeline import resting_limit
from roundtable.types import Candidate


def _c(price=81656.0, spread_bps=185, atr=2288.0, asset_class="crypto"):
    return Candidate(symbol="BTC-USD", asset_class=asset_class, price=price,
                     spread_bps=spread_bps, atr=atr)


def test_a_buy_is_improved_upward_toward_the_ask():
    """Upward for a buy: we offer more, so a seller can reach us sooner."""
    assert resting_limit(_c(), "buy") > _c().price


def test_a_sell_is_improved_downward_toward_the_bid():
    assert resting_limit(_c(), "sell") < _c().price


def test_the_improvement_never_exceeds_the_half_spread():
    """Beyond the touch is not resting, it is crossing — and crossing can never
    clear the net floor on a book this wide."""
    c = _c(atr=999_999.0)          # an ATR that would fund an enormous move
    half_spread = c.price * (c.spread_bps / 10_000.0) / 2
    assert resting_limit(c, "buy") <= c.price + half_spread + 1e-6


def test_a_quiet_name_earns_no_improvement():
    """Self-limiting. If the ATR cannot pay for any improvement, the order
    stays at the mark — the correct answer for a name too quiet to trade
    through this spread."""
    c = _c(atr=0.01)
    assert resting_limit(c, "buy") == pytest.approx(c.price, abs=0.05)


def test_the_improvement_is_bounded_by_the_graders_own_budget():
    """The tie that makes this principled rather than a tuned constant: the
    price improvement is the cost budget the net reward:risk floor permits."""
    from verification.criteria import DEFAULT_CRITERIA as C
    c = _c()
    f = C.min_net_reward_risk_ratio
    budget = c.atr * (3 - 2 * f) / (1 + f)
    assert resting_limit(c, "buy") - c.price <= budget + 1e-6


def test_the_improved_limit_still_clears_the_net_floor():
    """The whole point. An improvement that made the trade uneconomic would be
    the grader refusing what execution just created."""
    from verification.outcome_grader import DirectionalTrade
    c = _c()
    entry = resting_limit(c, "buy")
    trade = DirectionalTrade(
        symbol="BTC-USD", side="buy", size_usd=100.0, entry=entry,
        stop=entry - 2 * c.atr, target=entry + 3 * c.atr,
        win_probability=0.6, asset_class="crypto",
        spread_bps=c.spread_bps, rests=True,
        estimated_slippage_bps=int((entry - c.price) / c.price * 10_000))
    from verification.criteria import DEFAULT_CRITERIA as C
    assert trade.net_r_multiple >= C.min_net_reward_risk_ratio * 0.99


def test_no_atr_means_no_improvement():
    """Without an ATR there is no budget to compute, and guessing one would
    invent the licence to pay."""
    assert resting_limit(_c(atr=None), "buy") == pytest.approx(_c().price)


def test_no_spread_means_no_improvement():
    assert resting_limit(_c(spread_bps=None), "buy") == pytest.approx(_c().price)


def test_the_order_builder_uses_it():
    """Wiring. A pricing rule nothing calls is decoration."""
    from trading.pipeline import _session_order_kwargs
    kw = _session_order_kwargs(_c(), side="buy")
    assert kw["order_type"] == "limit"
    assert kw["limit_price"] > _c().price, "still resting at the bare mark"
