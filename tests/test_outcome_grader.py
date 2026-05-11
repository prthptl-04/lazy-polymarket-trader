from verification.criteria import VerifiedOutcomeCriteria
from verification.outcome_grader import OutcomeGrader, ProposedTrade


def _good_trade(**overrides) -> ProposedTrade:
    base = dict(
        market_id="m1",
        side="YES",
        size_usd=50.0,
        price=0.55,
        orderbook_depth_usd=1000.0,
        expected_edge_bps=30,
        estimated_slippage_bps=25,
    )
    base.update(overrides)
    return ProposedTrade(**base)


def test_accepts_well_formed_trade():
    g = OutcomeGrader()
    result = g.evaluate(_good_trade())
    assert result.passed
    assert result.rejected_rule is None


def test_rejects_oversize_position():
    g = OutcomeGrader(VerifiedOutcomeCriteria(max_position_usd=10.0))
    result = g.evaluate(_good_trade(size_usd=50.0))
    assert not result.passed
    assert result.rejected_rule == "max_position_usd"


def test_rejects_thin_orderbook():
    g = OutcomeGrader()
    result = g.evaluate(_good_trade(orderbook_depth_usd=100.0))
    assert not result.passed
    assert result.rejected_rule == "min_orderbook_depth_usd"


def test_rejects_negative_edge():
    g = OutcomeGrader()
    result = g.evaluate(_good_trade(expected_edge_bps=5))
    assert not result.passed
    assert result.rejected_rule == "min_expected_edge_bps"


def test_rejects_bad_side():
    g = OutcomeGrader()
    result = g.evaluate(_good_trade(side="MAYBE"))
    assert not result.passed
    assert result.rejected_rule == "side"


def test_rejects_price_out_of_range():
    g = OutcomeGrader()
    result = g.evaluate(_good_trade(price=1.5))
    assert not result.passed
    assert result.rejected_rule == "price_range"
