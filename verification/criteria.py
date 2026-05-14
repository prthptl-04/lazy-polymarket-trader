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


DEFAULT_CRITERIA = VerifiedOutcomeCriteria()

# The live executor requires at least this many graded paper trades for the
# active strategy before allowing live order submission. User-chosen.
MIN_PAPER_TRADES_FOR_LIVE: int = 50

# Sentinel lesson the user must record to flip live. Stored in agent_lessons
# under agent_id='*' so every specialist sees it on their next briefing.
LIVE_APPROVAL_LESSON: str = "live trading approved"
