"""Retail social chatter, scored for ATTENTION first and mood second.

Logic adapted from the sentiment bots the operator pointed at
(CyberPunkMetalHead/Cryptocurrency-Sentiment-Bot, Sam120204/Stock_Trading_Reddit,
indiser/market-sentiment-analyzer, coooins/crypto-subreddits-cli,
dylankilkenny/cryptosub, varunpillai/TwitterStockMarketSentimentAnalysis). No
upstream code is used — the shared pipeline is what transfers: fetch posts,
score them, aggregate per ticker.

Two departures from what those projects do, both measured rather than preferred.

**Stock VADER cannot read trading vernacular.** Measured before any change:

    +0.000  NVDA to the moon, loading up calls
    +0.000  this is going to zero, total rug

Both exactly neutral. Every one of those projects scores polarity with stock
VADER or TextBlob, so on the vocabulary their own data is written in they are
reading noise. Extending the lexicon fixes it (+0.670 / -0.648) while a
genuinely neutral sentence stays at zero — otherwise the extension has not added
vocabulary, it has added a thumb on the scale.

**Attention leads, polarity follows.** Unusual mention VOLUME is the half with
real literature behind it; retail polarity is close to a coin flip. So the note
leads with how loud a name has become relative to its OWN baseline and reports
mood second. That is the same reasoning the fund already applies to price:
volume confirms participation, a move on its own does not.

Reddit's public JSON now returns 403; the API needs a (free) OAuth client. With
no credentials this degrades to a stated reason, like every other source here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from statistics import mean
from typing import Any, Optional, Sequence

from trading.catalysts import CatalystEvidence

logger = logging.getLogger(__name__)

# Below this a mean is one person in a good mood, not a sentiment reading.
MIN_POSTS_FOR_MOOD = 8
# Mentions this many times the name's own baseline is unusual attention.
LOUD_VELOCITY = 3.0
# Above this, agreement is crowded rather than confirming.
CROWDED_SCORE = 0.6
CROWDED_POSTS = 100

# The vernacular VADER does not ship with. Values on VADER's own -4..+4 scale.
# Deliberately conservative: these are strong words in context, and scoring them
# at the extremes would let three posts swamp a hundred measured ones.
TRADING_LEXICON: dict[str, float] = {
    "moon": 2.5, "mooning": 2.5, "moonshot": 2.0, "ath": 1.5, "breakout": 1.5,
    "bullish": 2.0, "bull": 1.2, "calls": 1.0, "long": 0.8, "pump": 1.5,
    "squeeze": 1.2, "rip": 1.2, "ripping": 1.5, "printing": 1.5, "tendies": 2.0,
    "bearish": -2.0, "bear": -1.2, "puts": -1.0, "short": -0.8, "dump": -2.0,
    "dumping": -2.0, "rug": -3.0, "rugged": -3.0, "rugpull": -3.0,
    "rekt": -3.0, "bagholder": -2.5, "bagholding": -2.5, "capitulation": -2.5,
    "dead": -2.0, "worthless": -3.0, "scam": -3.0, "dumped": -2.0,
}

_ANALYZER: Any = None


def _analyzer() -> Any:
    """One analyzer, lexicon extended once. Rebuilding it per post is the
    single most expensive thing a naive implementation of this does."""
    global _ANALYZER
    if _ANALYZER is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        a = SentimentIntensityAnalyzer()
        a.lexicon.update(TRADING_LEXICON)
        _ANALYZER = a
    return _ANALYZER


def score_text(text: Optional[str]) -> float:
    """Compound polarity in -1..+1. Zero for anything unscoreable."""
    if not text:
        return 0.0
    try:
        return float(_analyzer().polarity_scores(str(text))["compound"])
    except Exception:
        logger.debug("sentiment scoring failed", exc_info=True)
        return 0.0


def mention_velocity(today: int, baseline: Sequence[int]) -> Optional[float]:
    """Mentions today against the name's own recent average.

    Ten mentions is loud for a small name and silence for NVDA, so the
    comparison has to be against the name's own history rather than a constant.

    `None` when there is no baseline: a first sighting is not an infinite
    spike, and dividing by zero here would rank every brand-new ticker above
    everything that has ever traded.
    """
    usable = [b for b in baseline if b is not None]
    if not usable:
        return None
    avg = mean(usable)
    if avg <= 0:
        return None
    return round(today / avg, 2)


@dataclass(frozen=True)
class SocialPulse:
    symbol: str
    posts: int
    mean_score: float
    velocity: Optional[float]
    top_quote: Optional[str] = None


def summarise_social(pulse: SocialPulse) -> str:
    """One derived line. Attention first, mood second, both qualified."""
    if pulse.posts <= 0:
        return (f"Social chatter: no chatter about {pulse.symbol} in the window "
                "checked. Silence, not an unchecked source.")

    parts: list[str] = []
    if pulse.velocity is None:
        parts.append(f"Social chatter: {pulse.posts} posts about {pulse.symbol}, "
                     "with no baseline to compare against — loudness unknown.")
    else:
        how = ("unusually loud" if pulse.velocity >= LOUD_VELOCITY
               else "quiet" if pulse.velocity < 0.5 else "normal")
        parts.append(f"Social chatter: {pulse.posts} posts about {pulse.symbol}, "
                     f"{pulse.velocity}x its own recent average — {how}.")

    if pulse.posts < MIN_POSTS_FOR_MOOD:
        parts.append(f"Too few posts ({pulse.posts}) to read a mood from; a mean "
                     "over this many is one person in a good mood.")
    else:
        mood = ("positive" if pulse.mean_score > 0.15
                else "negative" if pulse.mean_score < -0.15 else "mixed")
        parts.append(f"Mean tone {pulse.mean_score:+.2f} ({mood}).")
        if pulse.mean_score >= CROWDED_SCORE and pulse.posts >= CROWDED_POSTS:
            parts.append("That is CROWDED agreement, which is a risk factor "
                         "rather than a confirmation — everyone who is going to "
                         "buy on this may already have.")

    if pulse.top_quote:
        parts.append(f'Loudest post: "{pulse.top_quote[:120]}" — an unverified '
                     "opinion from a forum, never a fact about the business.")
    return " ".join(parts)


@dataclass
class SocialSentimentFeed:
    """Reddit chatter for one symbol. Optional, and degrades loudly."""

    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    user_agent: str = "project-dhan/1.0 (personal research)"
    subreddits: tuple[str, ...] = ("wallstreetbets", "stocks", "CryptoCurrency")
    timeout_seconds: float = 12.0

    def __post_init__(self) -> None:
        self.client_id = self.client_id or os.environ.get("REDDIT_CLIENT_ID")
        self.client_secret = self.client_secret or os.environ.get("REDDIT_CLIENT_SECRET")

    async def pulse_for(self, symbol: str) -> CatalystEvidence:
        if not (self.client_id and self.client_secret):
            return CatalystEvidence(
                reason="no Reddit credentials (REDDIT_CLIENT_ID / "
                       "REDDIT_CLIENT_SECRET). Reddit's public JSON returns 403; "
                       "the API needs a free OAuth client.")
        try:
            posts = await asyncio.wait_for(
                asyncio.to_thread(self._fetch, symbol), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return CatalystEvidence(reason="Reddit did not answer in time")
        except Exception as e:
            return CatalystEvidence(reason=f"Reddit unavailable: {type(e).__name__}")

        scored = [(score_text(p.get("title")), p) for p in posts]
        loudest = max(scored, key=lambda s: abs(s[0]), default=(0.0, {}))[1]
        pulse = SocialPulse(
            symbol=symbol, posts=len(posts),
            mean_score=round(mean([s for s, _ in scored]), 3) if scored else 0.0,
            velocity=None,          # baseline needs history this does not keep yet
            top_quote=loudest.get("title"),
        )
        return CatalystEvidence(notes=(summarise_social(pulse),), available=True)

    def _fetch(self, symbol: str) -> list[dict]:
        """Token, then search. Kept sync and run in a thread — one HTTP round
        trip per subreddit, never on the hot path."""
        import urllib.parse
        import urllib.request
        import base64
        import json

        creds = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()).decode()
        req = urllib.request.Request(
            "https://www.reddit.com/api/v1/access_token",
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {creds}",
                     "User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as r:
            token = json.loads(r.read())["access_token"]

        out: list[dict] = []
        term = _search_term(symbol)
        for sub in self.subreddits:
            url = (f"https://oauth.reddit.com/r/{sub}/search?"
                   + urllib.parse.urlencode(
                       {"q": term, "restrict_sr": "1", "sort": "new",
                        "t": "day", "limit": 50}))
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {token}", "User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as r:
                    body = json.loads(r.read())
            except Exception:
                logger.debug("reddit search failed for r/%s", sub, exc_info=True)
                continue
            out.extend(c.get("data", {})
                       for c in body.get("data", {}).get("children", []))
        return out


def _search_term(symbol: str) -> str:
    """`BTC-USD` -> `BTC`. Nobody posts the pair spelling."""
    return re.sub(r"-USD$", "", symbol.upper())


def _demo() -> None:
    assert score_text("NVDA to the moon, loading up calls") > 0.3
    assert score_text("this is going to zero, total rug") < -0.3
    assert abs(score_text("NVDA earnings tomorrow")) < 0.15
    assert mention_velocity(30, [10, 10, 10]) == 3.0
    assert mention_velocity(30, []) is None
    assert "no chatter" in summarise_social(
        SocialPulse("NVDA", 0, 0.0, 0.0)).lower()
    assert "too few" in summarise_social(
        SocialPulse("NVDA", 3, 0.9, 2.0)).lower()
    print("social_sentiment self-check passed")


if __name__ == "__main__":
    _demo()
