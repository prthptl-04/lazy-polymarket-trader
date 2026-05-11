"""Kelly position sizing for Polymarket binary cash-settled markets.

For a YES buy at `price` (which is the implied probability):
- Win pays `1 - price` per unit staked.
- Loss costs `price` per unit staked.

Kelly fraction (full Kelly):
    f* = (p - price) / (1 - price)

Half-Kelly is the default risk floor (kelly_multiplier=0.5). Always clamp to
`[0, max_position_usd / bankroll]` so a single position can never blow up
the bankroll.

The grader will still independently verify the resulting trade — these helpers
are necessary, not sufficient.
"""

from __future__ import annotations

from dataclasses import dataclass

from verification.criteria import DEFAULT_CRITERIA, VerifiedOutcomeCriteria


@dataclass(frozen=True)
class KellyResult:
    raw_fraction: float          # uncapped Kelly fraction in [-1, 1]
    capped_fraction: float       # after multiplier + zero floor
    size_usd: float              # final position size in USD
    edge_bps: int                # signed; > 0 means market underprices outcome
    reason: str                  # human-readable explanation


def _validate_prob(name: str, value: float) -> None:
    if not (0.0 < value < 1.0):
        raise ValueError(f"{name} must be strictly between 0 and 1 (got {value!r})")


def kelly_fraction(p: float, price: float) -> float:
    """Full Kelly fraction for a YES buy at `price` given probability `p`.

    Returns the SIGNED fraction. A negative result means the market is
    overpricing the outcome — switch sides (buy NO) before sizing.
    """
    _validate_prob("p", p)
    _validate_prob("price", price)
    return (p - price) / (1.0 - price)


def kelly_size_usd(
    *,
    p: float,
    price: float,
    bankroll_usd: float,
    criteria: VerifiedOutcomeCriteria = DEFAULT_CRITERIA,
    kelly_multiplier: float = 0.5,
) -> KellyResult:
    """Recommended position size for a YES buy. For NO buys, pass (1-p, 1-price)
    and treat the resulting size as a NO stake.

    bankroll_usd: total available capital to risk across the book.
    kelly_multiplier: 0.5 = half-Kelly (default). Conservative — full Kelly
        maximizes long-run growth but has heavy short-run variance.
    """
    if bankroll_usd <= 0:
        return KellyResult(0.0, 0.0, 0.0, 0, "bankroll is zero or negative")
    if not (0.0 < kelly_multiplier <= 1.0):
        raise ValueError(f"kelly_multiplier must be in (0, 1] (got {kelly_multiplier!r})")

    raw = kelly_fraction(p, price)
    edge_bps = int(round((p - price) * 10_000))

    if raw <= 0:
        return KellyResult(raw, 0.0, 0.0, edge_bps, "no edge on the YES side")

    capped_fraction = raw * kelly_multiplier
    raw_size = bankroll_usd * capped_fraction

    if raw_size > criteria.max_position_usd:
        return KellyResult(
            raw_fraction=raw,
            capped_fraction=criteria.max_position_usd / bankroll_usd,
            size_usd=criteria.max_position_usd,
            edge_bps=edge_bps,
            reason=f"Kelly suggested {raw_size:.2f} USD, capped to {criteria.max_position_usd} per criteria",
        )

    return KellyResult(
        raw_fraction=raw,
        capped_fraction=capped_fraction,
        size_usd=round(raw_size, 2),
        edge_bps=edge_bps,
        reason="half-Kelly within bankroll and position caps",
    )
