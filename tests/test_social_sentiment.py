"""Retail social chatter, scored for ATTENTION first and mood second.

Logic adapted from the sentiment bots the operator pointed at
(CyberPunkMetalHead/Cryptocurrency-Sentiment-Bot, Sam120204/Stock_Trading_Reddit,
indiser/market-sentiment-analyzer, coooins/crypto-subreddits-cli,
dylankilkenny/cryptosub, varunpillai/TwitterStockMarketSentimentAnalysis). None
of their code is used; the shared pipeline — fetch posts, score, aggregate per
ticker — is what transfers.

Two departures from what those repos do, both measured rather than preferred.

**Off-the-shelf VADER cannot read trading vernacular.** Measured on the stock
analyzer before any change:

    +0.000  NVDA to the moon, loading up calls
    +0.000  this is going to zero, total rug

Both exactly neutral. Every one of those projects scores polarity with stock
VADER or TextBlob, so on the vocabulary their own data is written in, they are
reading noise. Extending the lexicon with the vernacular fixes it (+0.670 and
-0.648), and a genuinely neutral sentence must stay at zero or the extension has
simply moved the bias.

**Attention leads, polarity follows.** Unusual mention VOLUME is the half with
real literature behind it; retail polarity is close to a coin. So the note leads
with how loud a name has become relative to its own baseline, and reports mood
as secondary — which is also the same reasoning the fund already applies to
price: volume confirms participation, a move alone does not.
"""

import pytest

from trading.social_sentiment import (
    MIN_POSTS_FOR_MOOD, SocialPulse, mention_velocity, score_text,
    summarise_social,
)


# ---------------------------------------------------------------- scoring

def test_the_vernacular_is_scored_rather_than_read_as_neutral():
    assert score_text("NVDA to the moon, loading up calls") > 0.3
    assert score_text("this is going to zero, total rug") < -0.3
    assert score_text("bagholder here, got rekt") < -0.3


def test_a_genuinely_neutral_sentence_stays_neutral():
    """If everything scores non-zero, the lexicon has not been extended — it
    has been given a thumb on the scale."""
    assert abs(score_text("NVDA earnings tomorrow")) < 0.15


def test_ordinary_english_still_works():
    """The extension must not break what VADER was already good at."""
    assert score_text("this is excellent news, really happy") > 0.3
    assert score_text("terrible, awful, a disaster") < -0.3


def test_empty_text_is_zero_not_an_error():
    assert score_text("") == 0.0
    assert score_text(None) == 0.0


# ---------------------------------------------------------------- attention

def test_velocity_measures_a_name_against_its_own_baseline():
    """Ten mentions is loud for a small name and silence for NVDA. The
    comparison has to be against the name's own history."""
    assert mention_velocity(today=30, baseline=[10, 10, 10]) == pytest.approx(3.0)


def test_no_baseline_yields_no_velocity_rather_than_infinity():
    """A first sighting is not an infinite spike, and a division by zero here
    would rank a brand-new ticker above everything."""
    assert mention_velocity(today=30, baseline=[]) is None
    assert mention_velocity(today=30, baseline=[0, 0]) is None


def test_silence_is_a_real_reading_not_a_missing_one():
    assert mention_velocity(today=0, baseline=[10, 10]) == pytest.approx(0.0)


# ---------------------------------------------------------------- the note

def _pulse(**kw):
    base = dict(symbol="NVDA", posts=40, mean_score=0.42,
                velocity=3.2, top_quote="NVDA to the moon")
    return SocialPulse(**{**base, **kw})


def test_the_note_leads_with_attention_not_mood():
    note = summarise_social(_pulse())
    assert note.index("3.2") < note.index("0.42"), note


def test_thin_chatter_reports_the_count_and_withholds_the_mood():
    """A mean of four posts is one person in a good mood. Reporting it as a
    sentiment reading is how a committee gets talked into a trade by a forum."""
    note = summarise_social(_pulse(posts=MIN_POSTS_FOR_MOOD - 1))
    assert "too few" in note.lower()
    assert "0.42" not in note


def test_silence_says_so_explicitly():
    note = summarise_social(_pulse(posts=0, mean_score=0.0, velocity=0.0))
    assert "no chatter" in note.lower() or "silent" in note.lower()


def test_the_note_warns_that_crowded_bullishness_is_a_risk():
    """The Sentiment seat's own discipline, carried into the evidence: loud
    agreement is a risk factor, not a confirmation."""
    note = summarise_social(_pulse(posts=500, mean_score=0.8, velocity=12.0))
    assert "crowded" in note.lower()


def test_a_quote_is_carried_but_never_as_a_fact():
    note = summarise_social(_pulse())
    assert "to the moon" in note
    assert "unverified" in note.lower() or "opinion" in note.lower()


# ---------------------------------------------------------------- degrading

def test_no_credentials_degrades_to_a_stated_reason():
    import asyncio
    from trading.social_sentiment import SocialSentimentFeed
    ev = asyncio.run(SocialSentimentFeed(client_id=None, client_secret=None)
                     .pulse_for("NVDA"))
    assert ev.available is False
    assert "credential" in ev.reason.lower() or "reddit" in ev.reason.lower()
