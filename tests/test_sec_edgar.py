"""SEC EDGAR fundamentals — the free replacement for an unentitled feed.

- Blind: no User-Agent => no request at all (SEC blocks unidentified callers,
  and a default would get the IP throttled for everyone on it).
- Blind: annual-only. A quarter mixed into a year-over-year comparison makes
  Piotroski's deltas meaningless.
- Edge: per-filer concept names, restatements, missing total assets.
"""

import pytest

from finance.quality import altman_z_score, piotroski_f_score
from trading.sec_edgar import CONCEPTS, SecEdgarFundamentals, _two_most_recent_annual


def _fact(val, end, form="10-K", fp="FY", filed="2025-11-01"):
    return {"val": val, "end": end, "form": form, "fp": fp, "filed": filed}


def _gaap(**concepts):
    return {name: {"units": {"USD": rows}} for name, rows in concepts.items()}


def _provider(payloads, ua="Test test@example.com"):
    calls = []

    def fetch(url, user_agent):
        calls.append(url)
        for key, body in payloads.items():
            if key in url:
                return body
        raise RuntimeError("404")

    p = SecEdgarFundamentals(user_agent=ua, fetch=fetch)
    p.calls = calls
    return p


TICKERS = {"0": {"ticker": "AAPL", "cik_str": 320193}}


def _facts(**kw):
    base = dict(
        Assets=[_fact(359e9, "2025-09-27"), _fact(364e9, "2024-09-28")],
        Liabilities=[_fact(285e9, "2025-09-27"), _fact(308e9, "2024-09-28")],
        OperatingIncomeLoss=[_fact(133e9, "2025-09-27"), _fact(123e9, "2024-09-28")],
    )
    base.update(kw)
    return {"facts": {"us-gaap": _gaap(**base)}}


# ---------------- the guard ----------------

@pytest.mark.asyncio
async def test_no_user_agent_makes_no_request():
    """SEC blocks unidentified callers; a default UA throttles the whole IP."""
    p = _provider({"company_tickers": TICKERS}, ua=None)
    assert await p.get_financials("AAPL") is None
    assert p.calls == []


@pytest.mark.asyncio
async def test_trust_gate_refusal_blocks_the_fetch():
    class _Refusing:
        def request_scrape(self, agent_id, target):
            class _O: approved, reason = False, "not allowlisted"
            return _O()

    p = _provider({"company_tickers": TICKERS, "companyfacts": _facts()})
    p.manager = _Refusing()
    assert await p.get_financials("AAPL") is None
    assert p.calls == []


# ---------------- extraction ----------------

@pytest.mark.asyncio
async def test_returns_current_and_prior_year():
    p = _provider({"company_tickers": TICKERS, "companyfacts": _facts()})
    cur, prior = await p.get_financials("AAPL")
    assert cur.total_assets == pytest.approx(359e9)
    assert prior.total_assets == pytest.approx(364e9)


@pytest.mark.asyncio
async def test_unknown_ticker_returns_none():
    p = _provider({"company_tickers": TICKERS, "companyfacts": _facts()})
    assert await p.get_financials("NOPE") is None


@pytest.mark.asyncio
async def test_missing_total_assets_returns_none():
    """Altman divides by it; without it nothing is computable."""
    facts = {"facts": {"us-gaap": _gaap(Liabilities=[_fact(1e9, "2025-09-27")])}}
    p = _provider({"company_tickers": TICKERS, "companyfacts": facts})
    assert await p.get_financials("AAPL") is None


@pytest.mark.asyncio
async def test_filer_specific_revenue_tag_is_found():
    """Apple tags revenue as RevenueFromContractWithCustomer..., not Revenues."""
    facts = _facts(RevenueFromContractWithCustomerExcludingAssessedTax=[
        _fact(416e9, "2025-09-27")])
    p = _provider({"company_tickers": TICKERS, "companyfacts": facts})
    cur, _ = await p.get_financials("AAPL")
    assert cur.revenue == pytest.approx(416e9)


# ---------------- annual-only + restatements ----------------

def test_quarters_are_excluded():
    gaap = _gaap(Assets=[
        _fact(100, "2025-09-27"),
        _fact(999, "2025-06-30", form="10-Q", fp="Q3"),
        _fact(90, "2024-09-28"),
    ])
    assert _two_most_recent_annual(gaap, ("Assets",)) == [100.0, 90.0]


def test_restatement_prefers_the_later_filing():
    gaap = _gaap(Assets=[
        _fact(100, "2025-09-27", filed="2025-11-01"),
        _fact(105, "2025-09-27", filed="2026-02-01"),
    ])
    assert _two_most_recent_annual(gaap, ("Assets",))[0] == 105.0


def test_candidate_order_is_respected():
    gaap = _gaap(Revenues=[_fact(50, "2025-01-01")])
    assert _two_most_recent_annual(gaap, ("Missing", "Revenues")) == [50.0]


def test_absent_concept_yields_nothing():
    assert _two_most_recent_annual({}, ("Assets",)) == []


def test_every_screen_input_has_a_concept():
    for needed in ("total_assets", "total_liabilities", "retained_earnings",
                   "ebit", "net_income", "operating_cash_flow", "revenue"):
        assert CONCEPTS.get(needed)


# ---------------- end to end into the screens ----------------

@pytest.mark.asyncio
async def test_feeds_the_quality_screens():
    import dataclasses
    facts = _facts(
        AssetsCurrent=[_fact(147e9, "2025-09-27"), _fact(152e9, "2024-09-28")],
        LiabilitiesCurrent=[_fact(165e9, "2025-09-27"), _fact(176e9, "2024-09-28")],
        RetainedEarningsAccumulatedDeficit=[_fact(-14e9, "2025-09-27"), _fact(-19e9, "2024-09-28")],
        NetIncomeLoss=[_fact(112e9, "2025-09-27"), _fact(93e9, "2024-09-28")],
        NetCashProvidedByUsedInOperatingActivities=[_fact(111e9, "2025-09-27"), _fact(118e9, "2024-09-28")],
        Revenues=[_fact(416e9, "2025-09-27"), _fact(391e9, "2024-09-28")],
    )
    p = _provider({"company_tickers": TICKERS, "companyfacts": facts})
    cur, prior = await p.get_financials("AAPL")

    z = altman_z_score(dataclasses.replace(cur, market_cap=4.8e12))
    assert z.zone == "safe"
    assert piotroski_f_score(cur, prior).score is not None


@pytest.mark.asyncio
async def test_massive_delegates_financials_to_edgar():
    from trading.massive_provider import MassiveProvider
    p = _provider({"company_tickers": TICKERS, "companyfacts": _facts()})
    m = MassiveProvider(api_key="k", financials=p)
    assert (await m.get_financials("AAPL"))[0].total_assets == pytest.approx(359e9)


@pytest.mark.asyncio
async def test_massive_without_a_delegate_returns_none():
    from trading.massive_provider import MassiveProvider
    assert await MassiveProvider(api_key="k").get_financials("AAPL") is None
