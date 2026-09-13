"""News into the Sentiment seat.

Massive's news endpoint carries a PER-TICKER sentiment with reasoning, which is
what makes it usable: an article about the whole sector is not evidence about
our symbol.

- Blind: the publisher's label reaches the seat marked as the publisher's, not
  as the fund's view. A vendor label is a data point, not a verdict.
- Blind: an article whose insights cover other tickers must not have another
  ticker's sentiment attributed to ours.
- Edge: no provider, a dead feed, no insights at all.
"""

import pytest

from trading.massive_provider import MassiveProvider


def _article(title, ticker=None, sentiment=None, why=None, others=()):
    insights = []
    if ticker:
        insights.append({"ticker": ticker, "sentiment": sentiment,
                         "sentiment_reasoning": why})
    insights.extend(others)
    return {"title": title, "article_url": "https://x.test/a",
            "published_utc": "2026-09-11T10:00:00Z",
            "publisher": {"name": "Test Wire"}, "insights": insights}


def _provider(articles):
    return MassiveProvider(api_key="k",
                           fetch=lambda url, key, timeout=None: {"results": articles})


# ---------------- extraction ----------------

@pytest.mark.asyncio
async def test_per_ticker_sentiment_is_picked_out():
    p = _provider([_article("Apple beats", "AAPL", "positive", "record services revenue")])
    out = await p.get_news("AAPL")
    assert out[0]["sentiment"] == "positive"
    assert "record services" in out[0]["why"]
    assert out[0]["publisher"] == "Test Wire"


@pytest.mark.asyncio
async def test_another_tickers_sentiment_is_not_attributed_to_ours():
    """A sector piece is not evidence about our symbol."""
    p = _provider([_article("Chips roundup", others=[
        {"ticker": "NVDA", "sentiment": "positive", "sentiment_reasoning": "AI demand"}])])
    out = await p.get_news("AAPL")
    assert out[0]["sentiment"] is None
    assert out[0]["why"] is None


@pytest.mark.asyncio
async def test_articles_without_insights_still_return_the_headline():
    p = _provider([{"title": "Plain headline", "publisher": "Wire"}])
    out = await p.get_news("AAPL")
    assert out[0]["title"] == "Plain headline" and out[0]["sentiment"] is None


@pytest.mark.asyncio
async def test_limit_is_respected():
    p = _provider([_article(f"n{i}", "AAPL", "neutral") for i in range(20)])
    assert len(await p.get_news("AAPL", limit=3)) == 3


@pytest.mark.asyncio
async def test_dead_feed_returns_nothing_rather_than_raising():
    def boom(url, key, timeout=None): raise OSError("503")
    assert await MassiveProvider(api_key="k", fetch=boom).get_news("AAPL") == []


@pytest.mark.asyncio
async def test_no_api_key_returns_nothing():
    assert await MassiveProvider(api_key=None).get_news("AAPL") == []


# ---------------- delivery to the seat ----------------

@pytest.mark.asyncio
async def test_notes_label_the_sentiment_as_the_publishers():
    """The seat must not read a vendor's label as the fund's conclusion."""
    from trading.fund import FundLoop

    class _Data:
        async def get_news(self, symbol, limit=5):
            return [{"title": "Apple beats", "sentiment": "positive",
                     "why": "record services revenue"}]

    notes = await FundLoop(router=None, pipeline=None, round_table=None,
                           data=_Data())._news_notes("AAPL")
    assert notes[0].startswith("[publisher sentiment: positive]")
    assert "record services revenue" in notes[0]


@pytest.mark.asyncio
async def test_a_provider_without_news_degrades_silently():
    from trading.fund import FundLoop

    class _Bare:
        pass

    assert await FundLoop(router=None, pipeline=None, round_table=None,
                          data=_Bare())._news_notes("AAPL") == ()


@pytest.mark.asyncio
async def test_a_failing_news_call_does_not_break_the_cycle():
    from trading.fund import FundLoop

    class _Angry:
        async def get_news(self, symbol, limit=5): raise RuntimeError("rate limited")

    assert await FundLoop(router=None, pipeline=None, round_table=None,
                          data=_Angry())._news_notes("AAPL") == ()


def test_sentiment_seat_is_told_not_to_trust_a_label():
    from roundtable.seats import SEATS_BY_ID
    prompt = SEATS_BY_ID["sentiment"].system_prompt
    assert "Crowded bullishness is a risk factor" in prompt
    assert "never treat a scraped opinion as a fact" in prompt.lower()
