"""Thin wrapper around py-clob-client.

Read-only methods (markets, order book) work without a wallet; signed methods
(post_order) require POLYMARKET_PRIVATE_KEY in the environment.

Imports of py_clob_client are deferred so the module can be imported in CI
without the dependency installed.
"""

from __future__ import annotations

import os
import time
from typing import Any


class PolymarketClient:
    DEFAULT_HOST = "https://clob.polymarket.com"

    def __init__(self, host: str | None = None) -> None:
        self.host = host or os.environ.get("POLYMARKET_CLOB_HOST", self.DEFAULT_HOST)
        self._client = None

    def _ensure(self):
        if self._client is not None:
            return self._client
        try:
            from py_clob_client.client import ClobClient
        except ImportError as e:
            raise RuntimeError(
                "py-clob-client is not installed. Run `uv sync` (or `pip install py-clob-client`)."
            ) from e

        key = os.environ.get("POLYMARKET_PRIVATE_KEY")
        funder = os.environ.get("POLYMARKET_FUNDER_ADDRESS")
        if key:
            self._client = ClobClient(self.host, key=key, chain_id=137, signature_type=1, funder=funder)
        else:
            self._client = ClobClient(self.host, chain_id=137)
        return self._client

    def get_markets(self) -> Any:
        return self._with_retry(lambda: self._ensure().get_markets())

    def get_order_book(self, token_id: str) -> Any:
        return self._with_retry(lambda: self._ensure().get_order_book(token_id))

    def post_order(self, order: Any) -> Any:
        """Signed order. Will raise unless POLYMARKET_PRIVATE_KEY is configured."""
        if not os.environ.get("POLYMARKET_PRIVATE_KEY"):
            raise RuntimeError("POLYMARKET_PRIVATE_KEY missing; cannot sign live orders.")
        return self._with_retry(lambda: self._ensure().post_order(order))

    @staticmethod
    def _with_retry(fn, attempts: int = 5, base_delay: float = 0.25, max_delay: float = 4.0) -> Any:
        delay = base_delay
        last_exc: Exception | None = None
        for _ in range(attempts):
            try:
                return fn()
            except Exception as e:
                last_exc = e
                time.sleep(delay)
                delay = min(delay * 2, max_delay)
        assert last_exc is not None
        raise last_exc
