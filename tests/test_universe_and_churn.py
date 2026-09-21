"""Three ways the cycle spent money or said nothing when it should have spoken.

**B8 — a silent empty universe.** `_universe_for` returns the crypto watchlist
whenever equities are shut, with no scout fallback. `config/fund.toml` ships
`crypto = []`, so on a weekend the fund produced `universe = []`, returned at
the early exit, and reported nothing at all: zero candidates, zero errors, zero
warnings, `cycles += 1`. A cycle that did nothing, and nothing said why. Per
rule #23 that is most of the week.

**B9 — the weekend handoff flattens crypto and re-buys it in the same cycle.**
`should_flatten_crypto` fires, then `_universe_for` still returns the crypto
watchlist because the session is CRYPTO_ONLY until 04:00. At 03:15 Monday: sell
BTC, deliberate BTC, buy BTC, flatten again at 03:20. At the 187bps spread
measured on Robinhood that is expensive, and in paper it manufactures round
trips that count toward the fifty.

**B21 — a stopped position is re-entered in the same cycle.** `run_cycle` exits
first and then looks for new ideas, which is the right order, but nothing told
the candidate builder the symbol had just hit its stop. The stop fires for a
reason and the fund immediately overrode it. Same for a `reason="signal"`
close, and crypto is PDT-exempt so the weekend book had no brake at all.
"""

from datetime import datetime, timedelta

import pytest

from trading.fund import CycleReport, FundLoop
from trading.sessions import EASTERN, session_at

WEDNESDAY = datetime(2026, 9, 16, 10, 0, tzinfo=EASTERN)
SUNDAY = datetime(2026, 9, 20, 12, 0, tzinfo=EASTERN)          # crypto only
MONDAY_EARLY = datetime(2026, 9, 21, 3, 15, tzinfo=EASTERN)    # flatten window


def _loop(**kw):
    return FundLoop(router=None, pipeline=None, round_table=None, data=None, **kw)


# ---------- B8: say why there is nothing to do ----------

@pytest.mark.asyncio
async def test_an_empty_crypto_watchlist_says_so(tmp_path):
    """A weekend cycle with no crypto configured must be distinguishable from
    a weekend cycle where nothing passed the screens."""
    from memory.store import MemoryStore

    loop = _loop(crypto_watchlist=(), equity_watchlist=("AAPL",),
                 memory=MemoryStore(db_path=str(tmp_path / "u.db")))
    report = await loop.run_cycle(SUNDAY, equity_usd=500.0, available_cash_usd=500.0)

    assert report.universe == []
    assert report.errors, "a cycle that can do nothing must say why"
    assert "crypto" in report.errors[0].lower()
    assert "FUND_CRYPTO_WATCHLIST" in report.errors[0]


@pytest.mark.asyncio
async def test_a_configured_weekend_universe_is_silent(tmp_path):
    """No false alarms once it is configured."""
    from memory.store import MemoryStore

    loop = _loop(crypto_watchlist=("BTC",),
                 memory=MemoryStore(db_path=str(tmp_path / "u2.db")))
    report = await loop.run_cycle(SUNDAY, equity_usd=500.0, available_cash_usd=500.0)
    assert report.universe == ["BTC"]


# ---------- B9: do not re-buy what the handoff is selling ----------

def test_the_flatten_window_trades_nothing():
    """Selling BTC and buying it back in the same cycle pays the spread twice
    and manufactures a round trip out of an accounting event."""
    loop = _loop(crypto_watchlist=("BTC", "ETH"))
    assert loop._universe_for(session_at(SUNDAY), SUNDAY) == ["BTC", "ETH"]
    assert loop._universe_for(session_at(MONDAY_EARLY), MONDAY_EARLY) == []


# ---------- B21: honour the stop that just fired ----------

def test_a_symbol_closed_this_cycle_is_not_re_entered():
    """The stop fired for a reason. Re-entering at the stop price overrides a
    risk decision the fund made seconds earlier."""
    loop = _loop(equity_watchlist=("AAPL", "MSFT"))
    loop._cooling_off = {"AAPL": WEDNESDAY.date()}

    assert loop._universe_for(session_at(WEDNESDAY), WEDNESDAY) == ["MSFT"]


def test_the_brake_releases_the_next_session():
    """A cooldown, not a ban."""
    loop = _loop(equity_watchlist=("AAPL",))
    loop._cooling_off = {"AAPL": WEDNESDAY.date()}

    tomorrow = WEDNESDAY + timedelta(days=1)
    assert loop._universe_for(session_at(tomorrow), tomorrow) == ["AAPL"]


def test_crypto_is_braked_too():
    """Crypto is PDT-exempt, so the weekend book has no other brake at all."""
    loop = _loop(crypto_watchlist=("BTC", "ETH"))
    loop._cooling_off = {"BTC": SUNDAY.date()}

    assert loop._universe_for(session_at(SUNDAY), SUNDAY) == ["ETH"]


@pytest.mark.asyncio
async def test_closing_a_position_starts_the_cooldown(tmp_path):
    """Wired, not just present: an exit must actually set the brake."""
    from tests.test_fund_e2e import _stack, _mark, _holdings, BANKROLL

    loop, venue, book, _ = _stack(tmp_path)
    await loop.run_cycle(WEDNESDAY, holdings=await _holdings(venue),
                         equity_usd=BANKROLL, available_cash_usd=BANKROLL)
    _mark(loop, venue, book.get("AAPL").plan.stop - 1.0)
    report = await loop.run_cycle(WEDNESDAY + timedelta(minutes=5),
                                  holdings=await _holdings(venue),
                                  equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    assert [e.get("reason") for e in report.exits] == ["stop"]
    assert report.submitted == [], "the same cycle must not re-buy it"
    assert "AAPL" in loop._cooling_off
