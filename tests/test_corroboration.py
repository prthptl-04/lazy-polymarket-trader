"""Fact corroboration + the Corroborator seat.

The gap: the Devil's Advocate attacks reasoning; nothing attacked the facts.

- Blind: zero corroborated fields must NOT read as trustworthy — "checked
  against nothing" is the failure mode this exists to catch.
- Blind: missing != mismatched. Absence is not disagreement.
- Edge: per-field tolerances, bools rejected as measurements, dead providers.
"""

import pytest

from roundtable.corroboration import DEFAULT_TOLERANCES, compare
from roundtable.corroborator import Corroborator
from roundtable.seats import ROUND_ONE_SEATS, SEATS_BY_ID
from trading.market_data import PriceHistory
from trading.venues.base import Quote
from finance.exits import Bar


# ---------------- comparison ----------------

def test_close_values_agree():
    r = compare("AAPL", {"price": 100.0}, {"price": 100.5})
    assert r.checks[0].status == "agree" and r.trustworthy


def test_distant_values_mismatch():
    r = compare("AAPL", {"price": 100.0}, {"price": 140.0})
    assert r.checks[0].status == "mismatch"
    assert not r.trustworthy


def test_missing_is_unverified_not_mismatch():
    """Absence is not disagreement — conflating them cries wolf on thin names."""
    r = compare("X", {"price": 100.0}, {})
    assert r.checks[0].status == "unverified"
    assert r.mismatches == []


def test_nothing_confirmed_is_not_trustworthy():
    """The headline guarantee: checked against nothing != clean."""
    r = compare("X", {"price": 100.0}, {})
    assert not r.trustworthy
    assert "single-sourced" in "\n".join(r.evidence_lines())


def test_empty_comparison_is_not_trustworthy():
    assert not compare("X", {}, {}).trustworthy


def test_tolerances_are_per_field():
    """2% is fine for price, not for share count."""
    loose = compare("X", {"price": 100.0}, {"price": 101.5})
    tight = compare("X", {"shares_outstanding": 100.0}, {"shares_outstanding": 101.5})
    assert loose.checks[0].status == "agree"
    assert tight.checks[0].status == "mismatch"


def test_custom_tolerance_overrides():
    r = compare("X", {"price": 100.0}, {"price": 140.0}, tolerances={"price": 0.5})
    assert r.checks[0].status == "agree"


def test_unknown_field_uses_the_fallback_tolerance():
    r = compare("X", {"weird": 100.0}, {"weird": 104.0})
    assert r.checks[0].tolerance == pytest.approx(0.05)
    assert r.checks[0].status == "agree"


def test_booleans_are_not_measurements():
    r = compare("X", {"flag": True}, {"flag": False})
    assert r.checks[0].status == "unverified"


def test_both_zero_agrees():
    assert compare("X", {"volume": 0}, {"volume": 0}).checks[0].status == "agree"


def test_price_tolerance_is_sane():
    assert DEFAULT_TOLERANCES["shares_outstanding"] < DEFAULT_TOLERANCES["price"]


def test_report_dict_shape():
    d = compare("AAPL", {"price": 100.0}, {"price": 140.0}).as_dict()
    for k in ("symbol", "trustworthy", "agreed", "mismatches", "unverified"):
        assert k in d


# ---------------- the corroborator ----------------

class _Provider:
    def __init__(self, price=None, closes=(), fail=False):
        self.price, self.closes, self.fail = price, closes, fail

    async def get_quote(self, symbol):
        if self.fail:
            raise RuntimeError("dead")
        return Quote(symbol=symbol, last=self.price) if self.price else None

    async def get_history(self, symbol, *, lookback=60):
        if self.fail or not self.closes:
            return None
        return PriceHistory(
            bars=tuple(Bar(high=c, low=c, close=c) for c in self.closes),
            closes=tuple(self.closes), volumes=tuple(1e6 for _ in self.closes),
        )


@pytest.mark.asyncio
async def test_agreeing_providers_are_trustworthy():
    c = Corroborator(primary=_Provider(100.0, [99, 100]),
                     secondary=_Provider(100.2, [99, 100]))
    assert (await c.run("AAPL")).trustworthy


@pytest.mark.asyncio
async def test_disagreeing_providers_flag_a_mismatch():
    c = Corroborator(primary=_Provider(100.0, [99, 100]),
                     secondary=_Provider(150.0, [99, 100]))
    r = await c.run("AAPL")
    assert any(m.field == "price" for m in r.mismatches)


@pytest.mark.asyncio
async def test_bar_count_disagreement_is_caught():
    """Different trading-day counts means one source has gaps."""
    c = Corroborator(primary=_Provider(100.0, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),
                     secondary=_Provider(100.0, [1, 2]))
    r = await c.run("AAPL")
    assert any(m.field == "bar_count" for m in r.mismatches)


@pytest.mark.asyncio
async def test_no_secondary_says_so_and_is_not_trustworthy():
    r = await Corroborator(primary=_Provider(100.0, [99, 100])).run("AAPL")
    assert not r.trustworthy
    assert "No secondary provider" in r.notes[0]


@pytest.mark.asyncio
async def test_a_dead_provider_degrades_to_unverified():
    c = Corroborator(primary=_Provider(100.0, [99, 100]), secondary=_Provider(fail=True))
    r = await c.run("AAPL")
    assert not r.trustworthy and r.unverified


# ---------------- scraping stays narrative ----------------

class _Browser:
    """Stands in for PlaywrightFetcher."""

    def __init__(self, approved=True, text="lots of chatter", status=200):
        self.approved, self.text, self.status = approved, text, status

    def fetch(self, url, **kw):
        class _R: pass
        r = _R()
        r.approved, r.status, r.text = self.approved, self.status, self.text
        r.error = "blocked by trust gate"
        r.ok = self.approved and 200 <= (self.status or 0) < 300 and bool(self.text)
        return r


@pytest.mark.asyncio
async def test_scraped_text_is_a_note_never_a_compared_number():
    c = Corroborator(primary=_Provider(100.0, [99, 100]),
                     secondary=_Provider(100.1, [99, 100]), browser=_Browser(), scrape_urls=("https://x.test/{symbol}",))
    r = await c.run("AAPL", scrape=True)
    assert any("unverified, narrative only" in n for n in r.notes)
    assert {ch.field for ch in r.checks} == {"price", "last_close", "volume", "bar_count"}


@pytest.mark.asyncio
async def test_a_blocked_scrape_is_reported_not_silent():
    c = Corroborator(primary=_Provider(100.0, [99, 100]), browser=_Browser(approved=False), scrape_urls=("https://x.test/{symbol}",))
    r = await c.run("AAPL", scrape=True)
    assert any("scrape gate" in n for n in r.notes)


@pytest.mark.asyncio
async def test_scraping_is_off_by_default():
    c = Corroborator(primary=_Provider(100.0, [99, 100]), browser=_Browser(), scrape_urls=("https://x.test/{symbol}",))
    r = await c.run("AAPL")
    assert not any("narrative" in n for n in r.notes)


# ---------------- the seat ----------------

def test_corroborator_sits_in_round_one():
    assert "corroborator" in {s.id for s in ROUND_ONE_SEATS}


def test_seat_is_told_not_to_vote_bearish_on_a_data_fault():
    prompt = SEATS_BY_ID["corroborator"].system_prompt
    assert "not bearish" in prompt
    assert "single-sourced" in prompt


@pytest.mark.asyncio
async def test_a_rendered_error_page_is_not_used_as_narrative():
    """A 403 body is an error page, not research."""
    c = Corroborator(primary=_Provider(100.0, [99, 100]),
                     browser=_Browser(status=403, text="Rate Threshold Exceeded"),
                     scrape_urls=("https://x.test/{symbol}",))
    r = await c.run("AAPL", scrape=True)
    assert any("unusable (status 403)" in n for n in r.notes)
    assert not any("narrative only" in n for n in r.notes)
