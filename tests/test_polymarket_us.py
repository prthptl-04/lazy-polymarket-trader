"""Polymarket US adapter.

- Blind: no creds => refuses to place, never calls the SDK.
- Blind: notional-sized order refused (contracts are whole units).
- Edge: defensive parsing of price dicts and unpinned response shapes.
"""
import pytest

from trading.venues.base import OrderRequest, VenueError
from trading.venues.polymarket_us import PolymarketUSVenue


class _Res:
    def __init__(self, **kw): self.__dict__.update(kw)


def _client(*, creds=True, bbo=None, positions=None, balances=None,
            create=None, raises=None):
    calls = []

    def mk(name, ret):
        def fn(*a, **kw):
            calls.append((name, a, kw))
            if raises:
                raise raises
            return ret
        return fn

    c = _Res(
        key_id="k" if creds else None,
        secret_key="s" if creds else None,
        calls=calls,
    )
    c.markets = _Res(bbo=mk("bbo", bbo or {"bid": "0.44", "ask": "0.46"}))
    c.portfolio = _Res(positions=mk("positions", positions or {"positions": []}))
    c.account = _Res(balances=mk("balances", balances or {"availableBalance": "250.0"}))
    c.orders = _Res(create=mk("create", create or {"orderId": "o-1"}),
                    cancel=mk("cancel", {"ok": True}))
    return c


def _buy(**kw):
    kw.setdefault("quantity", 100)
    return OrderRequest(symbol="chiefs-super-bowl", side="buy",
                        asset_class="prediction", order_type="limit",
                        limit_price=0.55, **kw)


def test_supports_prediction_only():
    v = PolymarketUSVenue(client=_client())
    assert v.supports("prediction")
    assert not v.supports("equity")


@pytest.mark.asyncio
async def test_quote_parses_bbo():
    q = await PolymarketUSVenue(client=_client()).get_quote("chiefs-super-bowl")
    assert q.bid == pytest.approx(0.44)
    assert q.mid == pytest.approx(0.45)


@pytest.mark.asyncio
async def test_quote_failure_raises_venue_error():
    v = PolymarketUSVenue(client=_client(raises=RuntimeError("boom")))
    with pytest.raises(VenueError):
        await v.get_quote("x")


@pytest.mark.asyncio
async def test_no_creds_refuses_to_place_and_never_calls_sdk():
    c = _client(creds=False)
    ack = await PolymarketUSVenue(client=c).place_order(_buy())
    assert not ack.accepted
    assert "read-only" in ack.error
    assert c.calls == []


@pytest.mark.asyncio
async def test_notional_order_is_refused():
    """Contracts are whole units; a dollar amount has no defined size."""
    ack = await PolymarketUSVenue(client=_client()).place_order(
        _buy(quantity=None, notional_usd=50.0)
    )
    assert not ack.accepted and "contracts" in ack.error


@pytest.mark.asyncio
async def test_order_payload_shape():
    c = _client()
    ack = await PolymarketUSVenue(client=c).place_order(_buy())
    _, args, _ = next(x for x in c.calls if x[0] == "create")
    payload = args[0]
    assert payload["intent"] == "ORDER_INTENT_BUY_LONG"
    assert payload["type"] == "ORDER_TYPE_LIMIT"
    assert payload["price"] == {"value": "0.5500", "currency": "USD"}
    assert ack.accepted and ack.venue_order_id == "o-1"


@pytest.mark.asyncio
async def test_sdk_error_becomes_a_rejection_not_a_crash():
    v = PolymarketUSVenue(client=_client(raises=RuntimeError("rate limited")))
    ack = await v.place_order(_buy())
    assert not ack.accepted and "rate limited" in ack.error


@pytest.mark.asyncio
async def test_positions_skip_zero_quantity():
    c = _client(positions={"positions": [
        {"marketSlug": "a", "quantity": "0", "averagePrice": "0.5"},
        {"marketSlug": "b", "quantity": "10", "averagePrice": "0.4"},
    ]})
    out = await PolymarketUSVenue(client=c).positions()
    assert [p.symbol for p in out] == ["b"]


@pytest.mark.asyncio
async def test_balances_fall_back_to_cash_for_equity():
    v = PolymarketUSVenue(client=_client(balances={"availableBalance": "250.0"}))
    acct = await v.account()
    assert acct.cash_usd == 250.0 and acct.equity_usd == 250.0


# ---------------- shapes verified against the live API 2026-09-12 ----------------

@pytest.mark.asyncio
async def test_account_parses_the_real_balances_envelope():
    live = {"balances": [{"currentBalance": 0.247, "currency": "USD",
                          "buyingPower": 0.247, "assetNotional": 0,
                          "displayedCash": 0.247, "availableToWithdraw": 0.247}]}
    acct = await PolymarketUSVenue(client=_client(balances=live)).account()
    assert acct.cash_usd == pytest.approx(0.247)
    assert acct.equity_usd == pytest.approx(0.247)


@pytest.mark.asyncio
async def test_equity_includes_open_position_value():
    """currentBalance alone would report a fully-invested account as empty."""
    live = {"balances": [{"currentBalance": 10.0, "buyingPower": 10.0,
                          "assetNotional": 90.0}]}
    acct = await PolymarketUSVenue(client=_client(balances=live)).account()
    assert acct.cash_usd == pytest.approx(10.0)
    assert acct.equity_usd == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_empty_positions_dict_is_handled():
    """Live API returns positions as {} (not []) when there are none."""
    live = {"positions": {}, "nextCursor": "", "eof": True, "availablePositions": []}
    assert await PolymarketUSVenue(client=_client(positions=live)).positions() == []
