"""Entry point: `python -m dashboard` boots the FastAPI app on 127.0.0.1:8765.

For the production wire-up, edit the runtime construction below to point at
your real PolymarketClient + Predictor + watched markets. The default below
runs with a stub client so the dashboard renders end-to-end even before you
have a wallet configured (everything stays in paper mode).
"""

from __future__ import annotations

import asyncio
import os

import uvicorn
from dotenv import load_dotenv

# Load .env BEFORE anything reads os.environ. Without this the entrypoint sees
# none of the configured keys: no ANTHROPIC_API_KEY means build_fund returns
# None and the dashboard starts with no fund attached, and no venue credentials
# means every balance reads as an error. Every other caller in this codebase
# loads it explicitly, so the omission here was invisible until the app was
# actually run.
load_dotenv()

from dashboard.fund_wiring import build_fund
from dashboard.runtime import build_runtime
from dashboard.server import create_app
from trading.fund_config import load_config
from memory.store import MemoryStore


def _arm_stack_dump() -> None:
    """`kill -USR1 <pid>` prints every thread's Python stack to stderr.

    Written after a wedged process cost half an hour of guessing. The event
    loop was blocked inside a coroutine and every endpoint — including
    /api/health — timed out, so nothing the process serves could say why.
    macOS `sample` gave C frames only, and py-spy needs root, which a
    non-interactive session does not have.

    Two lines, no cost while idle, and the next wedge names its own line
    number. It only ever prints: a diagnostic that could kill the process
    would be worse than the wedge.
    """
    import faulthandler
    import signal
    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True, chain=True)
    except Exception:  # pragma: no cover - platform without SIGUSR1
        pass

    # And the asyncio tasks, which faulthandler cannot see.
    #
    # Thread stacks show the event loop sitting in `select`, which is what a
    # HEALTHY idle loop looks like and what a loop with one stuck coroutine
    # also looks like. Twice in this session a cycle stopped advancing while
    # every thread stack said "fine", and both times the next step was
    # guesswork against the log. The pending coroutine is the answer, and
    # `asyncio.all_tasks()` has it.
    def _dump_tasks(_sig, _frame) -> None:
        import traceback
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            print("[tasks] no running loop", flush=True)
            return
        tasks = asyncio.all_tasks(loop)
        print(f"\n[tasks] {len(tasks)} pending", flush=True)
        for task in tasks:
            print(f"\n[task] {task.get_name()} done={task.done()} "
                  f"coro={task.get_coro()!r}", flush=True)
            try:
                for frame in task.get_stack(limit=12):
                    for line in traceback.format_stack(frame, limit=1):
                        print("  " + line.rstrip(), flush=True)
            except Exception as e:
                print(f"  <no stack: {e}>", flush=True)

    try:
        # SIGUSR2, so the two dumps can be taken independently.
        signal.signal(signal.SIGUSR2, _dump_tasks)
    except Exception:  # pragma: no cover - platform without SIGUSR2
        pass


def main() -> None:
    _arm_stack_dump()
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8765"))

    # One bankroll, one source. The dashboard used to read BANKROLL_USD
    # (default 100) while the fund sized against config.bankroll_usd (500), so
    # the equity curve and the drawdown denominator were five times too small —
    # every percentage a reader inferred was overstated fivefold. FundConfig
    # already resolves FUND_BANKROLL_USD > TOML > default; two env names for one
    # number is how they drifted apart.
    config = load_config()

    runtime = build_runtime(
        memory=MemoryStore(),
        starting_bankroll_usd=config.bankroll_usd,
    )

    # Attach the hedge-fund engine if it can be built. Returns None when the
    # watchlist is empty or no Anthropic key is set, in which case the
    # dashboard still runs and reports the fund as not attached.
    # The runtime watches the committee think. The fund does not know what a
    # dashboard is — it announces, and a failure in any watcher can never stop
    # it deliberating.
    runtime.fund_scheduler = build_fund(
        config=config, memory=runtime.memory,
        on_debate_start=runtime.begin_debate,
        on_opinion=runtime.record_opinion,
        on_thesis=lambda _thesis: runtime.finish_debate(),
    )
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

    # Polymarket is RETIRED (trading/venues/retired.py) and is deliberately not
    # constructed here. The adapter still exists and the router still refuses
    # it; leaving the registration out means the header strip, the balances map
    # and the engine list never learn about a venue the fund does not trade.

    # Robinhood, for READS only — balances and positions in the header. The
    # live gate still refuses any order to it, and execution routes to the
    # paper venue regardless.
    try:
        from trading.mcp_client import McpSession
        from trading.venues.robinhood import MCP_URL, RobinhoodVenue
        session = McpSession(server_url=MCP_URL)
        if session.auth_summary().get("authenticated"):
            runtime.venues["robinhood"] = RobinhoodVenue(session=session)
        else:
            print("[dashboard] Robinhood not authenticated; run scripts_mcp_auth.py")
    except Exception as e:
        print(f"[dashboard] Robinhood venue unavailable: {type(e).__name__}")

    # Charts and the positions table read through these.
    if runtime.fund_scheduler is not None:
        runtime.position_book = runtime.fund_scheduler.position_book
        runtime.data_provider = runtime.fund_scheduler.fund.data
        # Same feed the Catalyst seat reads, so the panel and the committee can
        # never be looking at different news.
        runtime.catalyst_feed = runtime.fund_scheduler.fund.catalysts

    # An operator who switched a venue off must not find it back on.
    runtime.restore_venue_sessions()

    app = create_app(runtime)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
