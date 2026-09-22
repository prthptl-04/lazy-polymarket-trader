"""A limit price survives instruments that trade below a cent.

Live error: `PEPE-USD: ValueError: limit_price must be positive`. PEPE trades
around 0.0000052, and the order builder rounded every limit to two decimals —
`round(0.0000052, 2)` is `0.0`, which `OrderRequest` correctly refuses.

The two-decimal round was written when the universe was BTC and ETH, where it is
harmless. The crypto scout then grew the universe to fifty-eight tradable pairs,
and roughly a fifth of them — PEPE, SHIB, BONK, XCN — trade below a cent. Every
one of those was unreachable, and the failure was per-candidate rather than
loud: the cycle logged one error and moved on.

Rounding now follows the instrument's magnitude rather than a constant. A price
is quantised to a tick fine enough to preserve it, and never to zero — a limit
rounded to zero is not a cheap order, it is an invalid one.
"""

import pytest

from trading.pipeline import round_to_tick


@pytest.mark.parametrize("price", [
    85_432.17891,        # BTC
    2_741.7732,          # ETH
    116.98101,           # SOL
    12.913169,           # LINK
    1.5208412,           # XRP
    0.1042317,           # DOGE
    0.0000052064,        # PEPE — the one that rounded to zero
    1e-08,               # finer than anything Robinhood lists
])
def test_relative_precision_is_preserved_on_any_instrument(price):
    """The property that matters. The limit was chosen against a cost budget
    measured in basis points, so a round that moved it by more than a basis
    point would spend budget nobody allocated."""
    out = round_to_tick(price)
    assert out > 0
    assert abs(out - price) / price < 1e-7, (price, out)


def test_a_sub_cent_price_never_rounds_to_zero():
    """The defect, stated directly. A limit at zero is not a cheap order, it is
    an invalid one — and `OrderRequest` rightly refuses it."""
    for price in (5.2e-06, 1e-08, 3.7e-05, 9.9e-07):
        assert round_to_tick(price) > 0, price


def test_the_rounded_price_stays_close_to_the_original():
    """Quantising must not move the price meaningfully — the limit was chosen
    by `resting_limit` against a cost budget, and a sloppy round would spend
    more of it than intended."""
    for price in (85_432.17891, 116.98101, 0.1042317, 5.2064e-06):
        assert abs(round_to_tick(price) - price) / price < 1e-4, price


def test_zero_and_negative_are_returned_unchanged():
    """Not this function's job to invent a price. The caller refuses them."""
    assert round_to_tick(0.0) == 0.0
    assert round_to_tick(-1.0) == -1.0


def test_the_order_builder_produces_a_usable_pepe_limit():
    """End to end: the exact candidate that errored in production."""
    from roundtable.types import Candidate
    from trading.pipeline import _session_order_kwargs
    pepe = Candidate(symbol="PEPE-USD", asset_class="crypto", price=5.2064e-06,
                     spread_bps=190, atr=3.1e-07, session="crypto_only")
    kwargs = _session_order_kwargs(pepe, side="buy")
    assert kwargs["order_type"] == "limit"
    assert kwargs["limit_price"] > 0
    # And it still rests inside the spread rather than crossing.
    ask = pepe.price * (1 + 0.019 / 2)
    assert pepe.price <= kwargs["limit_price"] < ask


def test_an_order_request_accepts_it():
    """The type that raised. If this passes, the production error is gone."""
    from roundtable.types import Candidate
    from trading.pipeline import _session_order_kwargs
    from trading.venues.base import OrderRequest
    pepe = Candidate(symbol="PEPE-USD", asset_class="crypto", price=5.2064e-06,
                     spread_bps=190, atr=3.1e-07, session="crypto_only")
    kwargs = _session_order_kwargs(pepe, side="buy")
    OrderRequest(symbol="PEPE-USD", side="buy", quantity=1_000_000.0,
                 asset_class="crypto", client_order_id="c1", **kwargs)
