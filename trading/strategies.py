"""Strategy interfaces. Real strategy logic is out of scope for Phase 0."""

from dataclasses import dataclass
from typing import Protocol

from verification.outcome_grader import ProposedTrade


@dataclass(frozen=True)
class MarketSnapshot:
    market_id: str
    yes_price: float
    no_price: float
    orderbook_depth_usd: float


class Strategy(Protocol):
    name: str

    def propose(self, snapshot: MarketSnapshot) -> ProposedTrade | None:
        """Return a proposed trade for this snapshot, or None to skip."""
        ...
