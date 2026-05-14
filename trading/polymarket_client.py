"""Polymarket CLOB client — real wallet integration.

Per docs.polymarket.com (researched 2026-05-13):

- Base URL `https://clob.polymarket.com`, chain_id 137 (Polygon mainnet).
- L1 (private key) signs an EIP-712 payload ONCE to derive L2 credentials.
- L2 credentials (apiKey, secret, passphrase) sign every subsequent request via
  HMAC-SHA256 in 5 headers: POLY_ADDRESS, POLY_API_KEY, POLY_SIGNATURE,
  POLY_TIMESTAMP, POLY_PASSPHRASE.
- Signature types: 1=POLY_PROXY, 2=GNOSIS_SAFE, 3=POLY_1271 (deposit wallet —
  recommended for new users; this is our default).
- `funder` is the address that HOLDS collateral. For deposit-wallet flows
  (type=3) `funder = POLYMARKET_FUNDER_ADDRESS`, distinct from the signer.

This module is the only place in the codebase allowed to construct a real
`ClobClient` or sign an order. Live execution still has to clear
`trading/execution.py`'s gates (CLAUDE.md rule #4).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ClobConfig:
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    signature_type: int = 3          # POLY_1271 (deposit wallet)

    @classmethod
    def from_env(cls) -> "ClobConfig":
        return cls(
            host=os.environ.get("POLYMARKET_CLOB_HOST", cls.host),
            chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", cls.chain_id)),
            signature_type=int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", cls.signature_type)),
        )


class PolymarketClient:
    """Thin façade around py-clob-client.

    Two construction modes:

    - read-only: no env vars → can do `get_markets`, `get_order_book`.
    - signed:    POLYMARKET_PRIVATE_KEY (+ POLYMARKET_FUNDER_ADDRESS) in env →
                 derives L2 creds on first signed call and caches them.
    """

    def __init__(self, config: ClobConfig | None = None, *, factory: Callable | None = None) -> None:
        self.config = config or ClobConfig.from_env()
        self._client = None
        self._creds = None
        # Injectable so tests don't need py-clob-client installed.
        self._factory = factory

    # ---------------- construction ----------------

    def _ensure_unsigned(self):
        if self._client is not None:
            return self._client
        ClobClient = self._resolve_clob_client()
        self._client = ClobClient(self.config.host, chain_id=self.config.chain_id)
        return self._client

    def _ensure_signed(self):
        if self._client is not None and self._creds is not None:
            return self._client

        key = os.environ.get("POLYMARKET_PRIVATE_KEY")
        funder = os.environ.get("POLYMARKET_FUNDER_ADDRESS")
        if not key:
            raise RuntimeError("POLYMARKET_PRIVATE_KEY missing — cannot construct signed client.")
        if self.config.signature_type in (1, 2, 3) and not funder:
            raise RuntimeError(
                "POLYMARKET_FUNDER_ADDRESS missing — required for proxy/safe/deposit signature_type."
            )

        ClobClient = self._resolve_clob_client()
        # First build with L1 key only to derive L2 creds.
        l1 = ClobClient(
            self.config.host,
            key=key,
            chain_id=self.config.chain_id,
            signature_type=self.config.signature_type,
            funder=funder,
        )
        self._creds = l1.create_or_derive_api_creds()
        # Re-construct with creds so requests are HMAC-signed at the L2 layer.
        self._client = ClobClient(
            self.config.host,
            key=key,
            chain_id=self.config.chain_id,
            signature_type=self.config.signature_type,
            funder=funder,
            creds=self._creds,
        )
        return self._client

    def _resolve_clob_client(self):
        if self._factory is not None:
            return self._factory
        try:
            from py_clob_client.client import ClobClient
        except ImportError as e:
            raise RuntimeError(
                "py-clob-client is not installed. Run `uv sync`."
            ) from e
        return ClobClient

    # ---------------- public ----------------

    def get_markets(self) -> Any:
        return _with_retry(lambda: self._ensure_unsigned().get_markets())

    def get_order_book(self, token_id: str) -> Any:
        return _with_retry(lambda: self._ensure_unsigned().get_order_book(token_id))

    def get_midpoint(self, token_id: str) -> Any:
        return _with_retry(lambda: self._ensure_unsigned().get_midpoint(token_id))

    def post_order(self, order: Any) -> Any:
        """Submit a signed order. Caller is `trading/execution.py` only.

        Live execution is gated by `Executor._live_trading_allowed()`. This
        method does NOT re-check the gate — that's the executor's job.
        """
        if not os.environ.get("POLYMARKET_PRIVATE_KEY"):
            raise RuntimeError("POLYMARKET_PRIVATE_KEY missing — cannot sign live orders.")
        return _with_retry(lambda: self._ensure_signed().post_order(order))

    def create_or_derive_api_creds(self):
        """Force the L1→L2 derivation explicitly. Returns the L2 creds."""
        self._ensure_signed()
        return self._creds


def _with_retry(fn, attempts: int = 5, base_delay: float = 0.25, max_delay: float = 4.0) -> Any:
    delay = base_delay
    last: BaseException | None = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(delay)
            delay = min(delay * 2, max_delay)
    assert last is not None
    raise last
