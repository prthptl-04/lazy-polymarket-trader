"""Entry point: `python -m dashboard` boots the FastAPI app on 127.0.0.1:8765.

For the production wire-up, edit the runtime construction below to point at
your real PolymarketClient + Predictor + watched markets. The default below
runs with a stub client so the dashboard renders end-to-end even before you
have a wallet configured (everything stays in paper mode).
"""

from __future__ import annotations

import os

import uvicorn

from dashboard.runtime import build_runtime
from dashboard.server import create_app
from decision_tree.predictor import Predictor
from decision_tree.tree import Tree
from memory.store import MemoryStore
from trading.autonomous_loop import WatchedMarket
from trading.polymarket_client import PolymarketClient
from trading.strategies import DecisionTreeStrategy


def main() -> None:
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8765"))

    client = PolymarketClient()           # read-only until a key is set
    watched: list[WatchedMarket] = []     # edit to add (market_id, token_id) pairs
    runtime = build_runtime(
        polymarket_client=client,
        watched=watched,
        memory=MemoryStore(),
        starting_bankroll_usd=float(os.environ.get("BANKROLL_USD", "100")),
        attach_feeds=True,
    )
    # Wire a baseline strategy. Replace the empty Tree with the trained one
    # once Trainer.fit has produced something useful.
    runtime.loop.strategy = DecisionTreeStrategy(
        name="baseline",
        cache=runtime.cache,
        predictor=Predictor(Tree()),
        bankroll_usd=runtime.starting_bankroll_usd,
    )
    if not runtime.user_feed_attached:
        print(
            "[dashboard] user-channel feed NOT attached (no L2 creds derivable). "
            "Positions and live P&L stay empty until a wallet is configured."
        )
    app = create_app(runtime)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
