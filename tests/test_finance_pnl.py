import math

import pytest

from finance.pnl import compute_pnl, equity_curve_from_trades


def _trade(market_id: str, side: str, size: float, price: float, *,
           grade_pass: bool = True, paper: bool = False, created: float = 0.0) -> dict:
    return {
        "market_id": market_id,
        "side": side,
        "size": size,
        "price": price,
        "grade_pass": int(grade_pass),
        "paper": int(paper),
        "created": created,
    }


# -------- compute_pnl --------

def test_pnl_yes_win():
    # YES at 0.40, stake 10. Win pays 10 * (1 - 0.40) = 6.0.
    pnl = compute_pnl([_trade("m1", "YES", 10, 0.40)], {"m1": "YES"})
    assert math.isclose(pnl, 6.0, abs_tol=1e-9)


def test_pnl_yes_loss():
    # YES at 0.40, stake 10. Loss costs 10 * 0.40 = 4.0.
    pnl = compute_pnl([_trade("m1", "YES", 10, 0.40)], {"m1": "NO"})
    assert math.isclose(pnl, -4.0, abs_tol=1e-9)


def test_pnl_no_win():
    # NO at 0.40 (so NO-price = 0.60). Win pays 10 * 0.40 = 4.0. Wait — see formula.
    # _pnl_for_trade: NO winning pays size * price = 10 * 0.40 = 4.0.
    # That's the convention: 'price' is the YES side; NO win pays size * price.
    pnl = compute_pnl([_trade("m1", "NO", 10, 0.40)], {"m1": "NO"})
    assert math.isclose(pnl, 4.0, abs_tol=1e-9)


def test_pnl_skips_paper_trades():
    pnl = compute_pnl([_trade("m1", "YES", 10, 0.40, paper=True)], {"m1": "YES"})
    assert pnl == 0.0


def test_pnl_skips_ungraded_trades():
    pnl = compute_pnl([_trade("m1", "YES", 10, 0.40, grade_pass=False)], {"m1": "YES"})
    assert pnl == 0.0


def test_pnl_unresolved_market_is_zero():
    pnl = compute_pnl([_trade("m1", "YES", 10, 0.40)], outcomes={})
    assert pnl == 0.0


def test_pnl_unknown_side_raises():
    with pytest.raises(ValueError):
        compute_pnl([_trade("m1", "MAYBE", 10, 0.40)], {"m1": "YES"})


def test_pnl_sums_multiple_trades():
    trades = [
        _trade("m1", "YES", 10, 0.40),   # win → +6.0
        _trade("m2", "YES", 10, 0.40),   # loss → -4.0
        _trade("m3", "NO", 10, 0.40),    # NO wins → +4.0
    ]
    outcomes = {"m1": "YES", "m2": "NO", "m3": "NO"}
    assert math.isclose(compute_pnl(trades, outcomes), 6.0, abs_tol=1e-9)


# -------- equity_curve_from_trades --------

def test_equity_curve_starts_at_bankroll():
    curve = equity_curve_from_trades([], outcomes={}, starting_bankroll=1_000)
    assert curve == [1_000]


def test_equity_curve_applies_pnl_in_order():
    trades = [
        _trade("m1", "YES", 10, 0.40, created=1.0),
        _trade("m2", "YES", 10, 0.40, created=2.0),
    ]
    curve = equity_curve_from_trades(
        trades, outcomes={"m1": "YES", "m2": "NO"}, starting_bankroll=1_000
    )
    assert curve[0] == 1_000
    assert math.isclose(curve[1], 1_006.0, abs_tol=1e-9)
    assert math.isclose(curve[2], 1_002.0, abs_tol=1e-9)


def test_equity_curve_negative_bankroll_raises():
    with pytest.raises(ValueError):
        equity_curve_from_trades([], outcomes={}, starting_bankroll=-1)
