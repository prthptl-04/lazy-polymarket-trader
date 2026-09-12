from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedOutcomeCriteria:
    """Acceptance criteria the Outcome Grader checks for every proposed trade.

    Defaults are sized for a $100 smoke-test bankroll (user-chosen 2026-05-13).
    Adjust before scaling — but never silently. Loosening these is a code
    review event, not a config tweak.
    """

    max_position_usd: float = 10.0          # 10% of a $100 bankroll per position
    max_daily_loss_usd: float = 20.0        # 20% of bankroll as the daily kill-switch
    min_orderbook_depth_usd: float = 500.0
    max_slippage_bps: int = 50              # 0.50%
    min_expected_edge_bps: int = 20         # 0.20%
    allowed_sides: tuple[str, ...] = ("YES", "NO")

    # ---- directional trades (equities / crypto) ----
    #
    # Prediction-market rules assume price is a probability and that an
    # orderbook depth number exists. Neither holds for "buy AAPL at $231", so
    # directional positions are graded on their own terms: the risk is not
    # bounded by the contract, it is bounded by the stop.

    allowed_directional_sides: tuple[str, ...] = ("buy", "sell")

    # The single most important rule in this file. A directional position with
    # no stop has unbounded downside, and the PDT rule means we hold it
    # overnight and cannot day-trade out of it. Entries without a stop are
    # refused outright.
    require_stop_loss: bool = True

    # Reward:risk floor. Below this the strategy needs an implausibly high hit
    # rate to break even — at 1.0R you must win >50% just to pay the spread.
    min_reward_risk_ratio: float = 1.5

    # A stop further than this from entry is not a stop, it is a hope. Also
    # caps how much of the book one position can lose at once.
    max_stop_distance_pct: float = 0.15

    # Liquidity proxy for venues with no visible orderbook depth.
    max_spread_bps: int = 50                # regular session
    max_spread_bps_extended: int = 100      # premarket / after-hours

    # Extended-hours books are thin and gappy; demand a better expected edge
    # there than during the regular session.
    extended_hours_edge_multiplier: float = 2.0


DEFAULT_CRITERIA = VerifiedOutcomeCriteria()

# The live executor requires at least this many graded paper trades for the
# active strategy before allowing live order submission. User-chosen.
MIN_PAPER_TRADES_FOR_LIVE: int = 50

# Sentinel lesson the user must record to flip live. Stored in agent_lessons
# under agent_id='*' so every specialist sees it on their next briefing.
LIVE_APPROVAL_LESSON: str = "live trading approved"
