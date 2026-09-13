"""Model spend, split by the mode that caused it.

- Blind: an untested mode must report earnings as UNKNOWN, not $0.00. Zero
  reads as break-even; null reads as untried, and only one of those is true.
- Blind: a failing ledger write must never take a deliberation down with it.
- Edge: the free tier is recorded at zero cost but still counted as calls.
"""

import pytest

from cache.cost_ledger import CACHE_READ_MULTIPLIER, CostLedger, Rates, usage_of
from dashboard.runtime import DashboardRuntime
from memory.store import MemoryStore


class _Usage:
    input_tokens = 1000
    output_tokens = 500
    cache_read_input_tokens = 20_000
    cache_creation_input_tokens = 2_000


class _Response:
    usage = _Usage()


def _ledger(tmp_path, **kw):
    return CostLedger(memory=MemoryStore(db_path=str(tmp_path / "c.db")), **kw)


def test_cache_reads_are_a_tenth_of_an_input_token():
    """The whole point of rule #2 — so the bill has to show it."""
    r = Rates(input_per_mtok=15.0, output_per_mtok=75.0)
    cached = r.cost_usd(input_tokens=0, output_tokens=0, cache_read=1_000_000)
    plain = r.cost_usd(input_tokens=1_000_000, output_tokens=0)
    assert cached == pytest.approx(plain * CACHE_READ_MULTIPLIER)


def test_usage_of_a_response_without_usage_is_zero_not_an_error():
    assert usage_of(object()) == {"input_tokens": 0, "output_tokens": 0,
                                  "cache_read": 0, "cache_write": 0}


def test_spend_is_tagged_with_the_mode_that_caused_it(tmp_path):
    led = _ledger(tmp_path, mode_provider=lambda: "live")
    led.record(provider="anthropic", model="m", response=_Response())
    assert led.summary()["modes"]["live"]["calls"] == 1
    assert led.summary()["modes"]["paper"]["calls"] == 0


def test_a_broken_mode_provider_falls_back_to_paper(tmp_path):
    """Mis-filing spend as live would overstate what live has cost."""
    def boom(): raise RuntimeError("no router")
    led = _ledger(tmp_path, mode_provider=boom)
    assert led.record(provider="anthropic", model="m", response=_Response())["mode"] == "paper"


def test_the_free_tier_is_zero_cost_but_still_counted(tmp_path):
    led = _ledger(tmp_path)
    row = led.record(provider="gemini", model="gemini-flash-latest", response=_Response())
    assert row["cost_usd"] == 0.0
    assert led.summary()["modes"]["paper"]["calls"] == 1


def test_a_failing_store_never_breaks_a_deliberation(tmp_path):
    class Broken:
        def record_llm_cost(self, **kw): raise RuntimeError("disk full")
    row = CostLedger(memory=Broken()).record(provider="anthropic", model="m",
                                             response=_Response())
    assert row["cost_usd"] > 0


def test_an_untested_mode_reports_unknown_earnings_not_zero(tmp_path):
    led = _ledger(tmp_path)
    led.record(provider="anthropic", model="m", response=_Response())
    modes = led.summary(earnings={"paper": 12.0, "live": None})["modes"]
    assert modes["paper"]["net_usd"] is not None
    assert modes["live"]["earned_usd"] is None and modes["live"]["net_usd"] is None


def test_pricing_says_where_its_numbers_came_from(tmp_path):
    pricing = _ledger(tmp_path).summary()["pricing"]
    assert "estimate" in pricing["source"] or "override" in pricing["source"]


def test_the_brief_the_seats_read_names_burn_and_earn(tmp_path):
    led = _ledger(tmp_path)
    led.record(provider="anthropic", model="m", response=_Response())
    brief = led.brief(led.summary(earnings={"paper": 40.0, "live": None}))
    assert "model calls" in brief and "earned per $1" in brief
    assert "live" not in brief          # a mode with no calls is not narrated


def test_dashboard_costs_survive_no_fund(tmp_path):
    rt = DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "d.db")))
    out = rt.costs()
    assert out["modes"]["paper"]["calls"] == 0
    assert out["modes"]["live"]["earned_usd"] is None
