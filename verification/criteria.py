from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedOutcomeCriteria:
    """Acceptance criteria the Outcome Grader checks for every proposed trade."""

    max_position_usd: float = 100.0
    max_daily_loss_usd: float = 50.0
    min_orderbook_depth_usd: float = 500.0
    max_slippage_bps: int = 50           # 0.50%
    min_expected_edge_bps: int = 20      # 0.20%
    allowed_sides: tuple[str, ...] = ("YES", "NO")


DEFAULT_CRITERIA = VerifiedOutcomeCriteria()
