"""Entry point: `python -m dashboard` boots the FastAPI app on 127.0.0.1:8765.

For the production wire-up, edit the runtime construction below to point at
your real PolymarketClient + Predictor + watched markets. The default below
runs with a stub client so the dashboard renders end-to-end even before you
have a wallet configured (everything stays in paper mode).
"""

from __future__ import annotations

import os

import uvicorn

from dashboard.fund_wiring import build_fund
from dashboard.runtime import build_runtime
from dashboard.server import create_app
from trading.fund_config import load_config
from memory.store import MemoryStore


def main() -> None:
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8765"))

    runtime = build_runtime(
        memory=MemoryStore(),
        starting_bankroll_usd=float(os.environ.get("BANKROLL_USD", "100")),
    )

    # Attach the hedge-fund engine if it can be built. Returns None when the
    # watchlist is empty or no Anthropic key is set, in which case the
    # dashboard still runs and reports the fund as not attached.
    config = load_config()
    runtime.fund_scheduler = build_fund(config=config, memory=runtime.memory)
    if runtime.fund_scheduler is None:
        print(
            "[dashboard] fund NOT attached — "
            + ("; ".join(config.warnings) or "see config/fund.toml and ANTHROPIC_API_KEY")
        )
    else:
        print(
            f"[dashboard] fund attached: "
            f"{len(config.equity_watchlist)} equities, "
            f"{len(config.crypto_watchlist)} crypto, "
            f"${config.bankroll_usd:,.0f} bankroll, "
            f"${config.max_daily_loss_usd:,.0f} daily loss cap"
        )
        for warning in config.warnings:
            print(f"[dashboard]   warning: {warning}")

    # Header balance strip. Best-effort: a venue that cannot be constructed
    # (missing SDK, missing creds) simply does not appear.
    try:
        from trading.venues.polymarket_us import PolymarketUSVenue
        runtime.venues["polymarket_us"] = PolymarketUSVenue()
    except Exception as e:
        print(f"[dashboard] Polymarket US venue unavailable: {type(e).__name__}")

    # Charts and the positions table read through these.
    if runtime.fund_scheduler is not None:
        runtime.position_book = runtime.fund_scheduler.position_book
        runtime.data_provider = runtime.fund_scheduler.fund.data

    # An operator who switched a venue off must not find it back on.
    runtime.restore_venue_sessions()

    app = create_app(runtime)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
