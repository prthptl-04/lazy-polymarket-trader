"""Fundamental quality screens — Altman Z and Piotroski F.

Both are deterministic scores over financial-statement inputs, and both run
BEFORE the round table convenes. That ordering is the point: a seat-by-seat
deliberation costs real tokens, so the fund should never spend one debating a
company that is visibly heading for bankruptcy or has deteriorating
fundamentals. Cheap arithmetic first, expensive judgement second.

Implemented from the published definitions:
  - Altman, E. "Financial Ratios, Discriminant Analysis and the Prediction of
    Corporate Bankruptcy", Journal of Finance, 1968.
  - Piotroski, J. "Value Investing: The Use of Historical Financial Statement
    Information to Separate Winners from Losers", Journal of Accounting
    Research, 2000.

Neither model is a verdict. Altman was fitted on 1960s manufacturers and reads
poorly on asset-light software and on banks; Piotroski was designed to sort
*within* a high book-to-market universe, not across the whole market. They are
filters, and the fund treats them as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


ZoneLabel = Literal["distress", "grey", "safe", "unknown"]

# Altman's original thresholds for public manufacturers.
DISTRESS_THRESHOLD = 1.81
SAFE_THRESHOLD = 2.99


@dataclass(frozen=True)
class Financials:
    """The statement lines both screens need. All in the same currency units."""

    total_assets: float
    total_liabilities: float
    current_assets: float = 0.0
    current_liabilities: float = 0.0
    retained_earnings: float = 0.0
    ebit: float = 0.0
    revenue: float = 0.0
    market_cap: float = 0.0
    net_income: float = 0.0
    operating_cash_flow: float = 0.0
    long_term_debt: float = 0.0
    shares_outstanding: float = 0.0
    gross_profit: float = 0.0


@dataclass(frozen=True)
class AltmanResult:
    score: Optional[float]
    zone: ZoneLabel
    components: dict
    reason: str

    @property
    def is_distressed(self) -> bool:
        return self.zone == "distress"


@dataclass(frozen=True)
class PiotroskiResult:
    score: Optional[int]          # 0-9
    signals: dict
    reason: str

    @property
    def is_strong(self) -> bool:
        return self.score is not None and self.score >= 7

    @property
    def is_weak(self) -> bool:
        return self.score is not None and self.score <= 3


def altman_z_score(f: Financials) -> AltmanResult:
    """Altman Z for a public company.

    Z = 1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·X4 + 1.0·X5

    X1 working capital / total assets     (short-term liquidity)
    X2 retained earnings / total assets   (cumulative profitability, age proxy)
    X3 EBIT / total assets                (operating productivity)
    X4 market cap / total liabilities     (equity cushion over debt)
    X5 revenue / total assets             (asset turnover)
    """
    if f.total_assets <= 0:
        return AltmanResult(None, "unknown", {}, "total_assets must be positive")
    if f.total_liabilities <= 0:
        # No leverage at all makes X4 undefined; it is also not a distress case.
        return AltmanResult(
            None, "unknown", {},
            "total_liabilities must be positive to evaluate the equity cushion",
        )

    working_capital = f.current_assets - f.current_liabilities
    x1 = working_capital / f.total_assets
    x2 = f.retained_earnings / f.total_assets
    x3 = f.ebit / f.total_assets
    x4 = f.market_cap / f.total_liabilities
    x5 = f.revenue / f.total_assets

    score = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5

    if score < DISTRESS_THRESHOLD:
        zone: ZoneLabel = "distress"
    elif score > SAFE_THRESHOLD:
        zone = "safe"
    else:
        zone = "grey"

    return AltmanResult(
        score=round(score, 4),
        zone=zone,
        components={"x1": x1, "x2": x2, "x3": x3, "x4": x4, "x5": x5},
        reason=f"Z={score:.2f} → {zone}",
    )


def piotroski_f_score(current: Financials, prior: Financials) -> PiotroskiResult:
    """Piotroski F-score: nine binary tests, one point each.

    Profitability (4): positive ROA, positive operating cash flow, improving
    ROA, and accruals quality (CFO exceeding net income — earnings backed by
    cash rather than by accounting).

    Leverage & liquidity (3): falling long-term leverage, rising current ratio,
    no share issuance (dilution is a negative signal).

    Efficiency (2): rising gross margin, rising asset turnover.

    Needs both years; `prior` supplies the deltas. 8-9 is strong, 0-2 weak.
    """
    if current.total_assets <= 0 or prior.total_assets <= 0:
        return PiotroskiResult(None, {}, "total_assets must be positive in both periods")

    roa = current.net_income / current.total_assets
    prior_roa = prior.net_income / prior.total_assets

    signals: dict[str, bool] = {}

    # --- profitability ---
    signals["positive_roa"] = roa > 0
    signals["positive_operating_cash_flow"] = current.operating_cash_flow > 0
    signals["improving_roa"] = roa > prior_roa
    # Accruals: cash earnings should exceed accounting earnings.
    signals["accruals_quality"] = current.operating_cash_flow > current.net_income

    # --- leverage, liquidity, funding ---
    cur_lev = current.long_term_debt / current.total_assets
    prior_lev = prior.long_term_debt / prior.total_assets
    signals["decreasing_leverage"] = cur_lev < prior_lev

    cur_ratio = _safe_ratio(current.current_assets, current.current_liabilities)
    prior_ratio = _safe_ratio(prior.current_assets, prior.current_liabilities)
    signals["improving_current_ratio"] = (
        cur_ratio is not None and prior_ratio is not None and cur_ratio > prior_ratio
    )

    # Dilution check. Equal counts pass — only an increase is penalised.
    signals["no_dilution"] = current.shares_outstanding <= prior.shares_outstanding

    # --- operating efficiency ---
    cur_margin = _safe_ratio(current.gross_profit, current.revenue)
    prior_margin = _safe_ratio(prior.gross_profit, prior.revenue)
    signals["improving_gross_margin"] = (
        cur_margin is not None and prior_margin is not None and cur_margin > prior_margin
    )

    cur_turnover = current.revenue / current.total_assets
    prior_turnover = prior.revenue / prior.total_assets
    signals["improving_asset_turnover"] = cur_turnover > prior_turnover

    score = sum(1 for passed in signals.values() if passed)
    return PiotroskiResult(
        score=score,
        signals=signals,
        reason=f"F={score}/9",
    )


def _safe_ratio(numerator: float, denominator: float) -> Optional[float]:
    """None rather than a divide-by-zero, so a missing statement line reads as
    'unknown' instead of silently scoring a point."""
    if not denominator:
        return None
    return numerator / denominator
