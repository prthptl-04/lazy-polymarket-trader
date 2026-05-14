import pytest

from trading.polymarket_client import ClobConfig, PolymarketClient


def test_clob_config_defaults_match_docs():
    c = ClobConfig()
    assert c.host == "https://clob.polymarket.com"
    assert c.chain_id == 137
    assert c.signature_type == 3


def test_clob_config_from_env(monkeypatch):
    monkeypatch.setenv("POLYMARKET_CLOB_HOST", "https://sandbox.polymarket.com")
    monkeypatch.setenv("POLYMARKET_CHAIN_ID", "80001")
    monkeypatch.setenv("POLYMARKET_SIGNATURE_TYPE", "2")
    c = ClobConfig.from_env()
    assert c.host == "https://sandbox.polymarket.com"
    assert c.chain_id == 80001
    assert c.signature_type == 2


def test_post_order_refuses_without_private_key(monkeypatch):
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    client = PolymarketClient()
    with pytest.raises(RuntimeError, match="POLYMARKET_PRIVATE_KEY missing"):
        client.post_order({"market_id": "m1", "side": "YES", "size_usd": 1, "price": 0.5})


class _FakeClob:
    """Stands in for py_clob_client.client.ClobClient."""
    def __init__(self, host, key=None, chain_id=None, signature_type=None, funder=None, creds=None):
        self.host = host
        self.key = key
        self.chain_id = chain_id
        self.signature_type = signature_type
        self.funder = funder
        self.creds = creds

    def create_or_derive_api_creds(self):
        return {"apiKey": "ak", "secret": "sk", "passphrase": "pw"}

    def get_markets(self):
        return [{"id": "m1"}]


def test_signed_client_construction_with_injected_factory(monkeypatch):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + "ab" * 32)
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0x" + "cd" * 20)
    client = PolymarketClient(factory=_FakeClob)
    creds = client.create_or_derive_api_creds()
    assert creds == {"apiKey": "ak", "secret": "sk", "passphrase": "pw"}


def test_unsigned_calls_work_without_keys():
    client = PolymarketClient(factory=_FakeClob)
    markets = client.get_markets()
    assert markets == [{"id": "m1"}]
