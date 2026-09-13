"""SEC EDGAR XBRL — free fundamentals for the quality screens.

Massive's $29 plan returns NOT_ENTITLED for financial statements, and
Robinhood's `get_sec_filing_facts` is MCP-only so the daemon cannot reach it.
EDGAR is the same underlying data — companies file it — served free, no key, no
entitlement. It is the correct source rather than a workaround.

One call per company: `/api/xbrl/companyfacts/CIK##########.json` returns every
tagged concept across every filing. We pick the two most recent annual values
per concept, which is exactly what Altman (current) and Piotroski (current +
prior) need.

Two things that bite here and are handled:

**Concept names vary by filer.** Apple tags revenue as
`RevenueFromContractWithCustomerExcludingAssessedTax`, not `Revenues`. Each
field therefore has a candidate list, tried in order.

**SEC requires a declaring User-Agent** and rate-limits to ~10 req/s. Requests
without a contact header get blocked, so `SEC_USER_AGENT` is mandatory and the
fetch refuses rather than sending a default that would get the IP throttled.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from finance.quality import Financials

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
MIN_INTERVAL_SECONDS = 0.12       # SEC allows ~10 req/s; stay under it.

# Ordered candidates per field. First hit wins.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities", "LiabilitiesAndStockholdersEquity"),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit",),
    "ebit": ("OperatingIncomeLoss", "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet"),
    "gross_profit": ("GrossProfit",),
    "shares_outstanding": ("CommonStockSharesOutstanding", "CommonStockSharesIssued"),
}


@dataclass
class SecEdgarFundamentals:
    """Fetches two years of statement lines. `fetch` is injectable for tests."""

    user_agent: Optional[str] = None
    fetch: Any = None
    manager: Any = None               # OrchestrationManager, for the rule-#8 gate
    agent_id: str = "research"
    _cik_map: dict[str, int] = field(default_factory=dict, init=False)
    _last_call: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.user_agent = self.user_agent or os.environ.get("SEC_USER_AGENT")
        self.fetch = self.fetch or self._http_get

    # ---------- MarketDataProvider-compatible ----------

    async def get_financials(
        self, symbol: str
    ) -> Optional[tuple[Financials, Optional[Financials]]]:
        cik = self._cik_for(symbol)
        if cik is None:
            return None
        facts = self._get(FACTS_URL.format(cik=cik))
        if not facts:
            return None

        us_gaap = ((facts.get("facts") or {}).get("us-gaap") or {})
        current: dict[str, float] = {}
        prior: dict[str, float] = {}
        for field_name, candidates in CONCEPTS.items():
            values = _two_most_recent_annual(us_gaap, candidates)
            if values:
                current[field_name] = values[0]
                if len(values) > 1:
                    prior[field_name] = values[1]

        if "total_assets" not in current:
            # Altman divides by total assets; without it nothing is computable.
            return None
        return (
            Financials(**{k: v for k, v in current.items() if k in _FIELDS}),
            Financials(**{k: v for k, v in prior.items() if k in _FIELDS}) if prior else None,
        )

    # ---------- internals ----------

    def _cik_for(self, symbol: str) -> Optional[int]:
        if not self._cik_map:
            data = self._get(TICKERS_URL)
            if not data:
                return None
            rows = data.values() if isinstance(data, dict) else data
            self._cik_map = {
                str(r["ticker"]).upper(): int(r["cik_str"])
                for r in rows if isinstance(r, dict) and r.get("ticker")
            }
        return self._cik_map.get(symbol.upper())

    def _get(self, url: str) -> Optional[dict]:
        if not self.user_agent:
            # SEC blocks unidentified callers; sending a default would get the
            # IP throttled for everyone on it.
            return None
        if self.manager is not None:
            outcome = self.manager.request_scrape(self.agent_id, url)
            if not outcome.approved:
                return None
        self._throttle()
        try:
            return self.fetch(url, self.user_agent)
        except Exception:
            return None

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < MIN_INTERVAL_SECONDS:
            time.sleep(MIN_INTERVAL_SECONDS - gap)
        self._last_call = time.monotonic()

    @staticmethod
    def _http_get(url: str, user_agent: str) -> dict:
        # SEC requires a declaring User-Agent. urllib sets Host itself.
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=30) as r:   # noqa: S310 — https, sec.gov
            return json.load(r)


_FIELDS = {f for f in Financials.__dataclass_fields__}


def _two_most_recent_annual(us_gaap: dict, candidates: tuple[str, ...]) -> list[float]:
    """Most recent two annual (FY, 10-K) values for the first concept that has any.

    Annual only: mixing a quarter into a year-over-year comparison would make
    Piotroski's deltas meaningless.
    """
    for concept in candidates:
        node = us_gaap.get(concept)
        if not isinstance(node, dict):
            continue
        rows: list[dict] = []
        for unit_rows in (node.get("units") or {}).values():
            rows.extend(r for r in unit_rows
                        if r.get("form") == "10-K" and r.get("fp") == "FY"
                        and r.get("val") is not None and r.get("end"))
        if not rows:
            continue
        # Dedupe by period end — the same figure is restated across filings.
        by_end: dict[str, dict] = {}
        for r in rows:
            end = r["end"]
            if end not in by_end or (r.get("filed", "") > by_end[end].get("filed", "")):
                by_end[end] = r
        ordered = sorted(by_end.values(), key=lambda r: r["end"], reverse=True)
        return [float(r["val"]) for r in ordered[:2]]
    return []
