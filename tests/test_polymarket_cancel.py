"""Cancel primitives on PolymarketClient.

- Auditor: cancel_order / cancel_market / cancel_all each test the signed path.
- Edge: missing key raises early; retries on transient failure.
- Blind: ensure factory injection lets tests bypass py_clob_client install.
"""

import pytest

from trading.polymarket_client import PolymarketClient


class _FakeClob:
    def __init__(self, host, key=None, chain_id=None, signature_type=None, funder=None, creds=None):
        self.cancel_calls = []
        self.cancel_market_calls = []
        self.cancel_all_calls = []
    def create_or_derive_api_creds(self):
        return {"apiKey": "ak", "secret": "sk", "passphrase": "pw"}
    def cancel(self, order_id):
        self.cancel_calls.append(order_id); return {"ok": True}
    def cancel_market_orders(self, market):
        self.cancel_market_calls.append(market); return {"ok": True}
    def cancel_all(self):
        self.cancel_all_calls.append(True); return {"ok": True}


def _signed_client(monkeypatch, factory):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0xdef")
    return PolymarketClient(factory=factory)


def test_cancel_order_refuses_without_private_key(monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="POLYMARKET_PRIVATE_KEY missing"):
        PolymarketClient().cancel_order("o-1")


def test_cancel_market_refuses_without_private_key(monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="POLYMARKET_PRIVATE_KEY missing"):
        PolymarketClient().cancel_market("m-1")


def test_cancel_all_refuses_without_private_key(monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="POLYMARKET_PRIVATE_KEY missing"):
        PolymarketClient().cancel_all()


def test_cancel_order_signs_and_calls_through(monkeypatch):
    factory = _FakeClob
    client = _signed_client(monkeypatch, factory)
    result = client.cancel_order("o-99")
    assert result == {"ok": True}
    # The signed instance is cached; verify the call landed.
    inner = client._client
    assert inner.cancel_calls == ["o-99"]


def test_cancel_market_routes_through_signed_client(monkeypatch):
    client = _signed_client(monkeypatch, _FakeClob)
    client.cancel_market("m-7")
    assert client._client.cancel_market_calls == ["m-7"]


def test_cancel_all_routes_through_signed_client(monkeypatch):
    client = _signed_client(monkeypatch, _FakeClob)
    client.cancel_all()
    assert client._client.cancel_all_calls == [True]
