"""Market scout — the fund finds its own candidates.

The watchlist is gone; one Massive call returns every US ticker and arithmetic
narrows ~12,500 rows to a handful BEFORE any LLM is involved.

- Blind: the screen must run before the round table; 12k tickers through a
  deliberation would cost more than the account is worth.
- Edge: weekends have no session, penny stocks and huge movers are excluded.
"""

from datetime import date

import pytest

from trading.discovery import MarketScout, ScoutCandidate


def _row(sym, close, open_, vol, n=1000):
    return {"T": sym, "c": close, "o": open_, "v": vol, "n": n}


def _scout(rows, **kw):
    return MarketScout(api_key="k", fetch=lambda url, key: {"results": rows}, **kw)


def test_liquid_mover_is_picked_up():
    s = _scout([_row("AAPL", 100.0, 95.0, 5_000_000)])
    out = s.scan(day=date(2026, 9, 11))
    assert [c.symbol for c in out] == ["AAPL"]
    assert out[0].move_pct == pytest.approx(5.26, abs=0.1)


def test_penny_stocks_are_excluded():
    """Sub-$5 spreads eat any edge."""
    assert _scout([_row("PENNY", 2.0, 1.8, 50_000_000)]).scan(day=date(2026, 9, 11)) == []


def test_illiquid_names_are_excluded():
    """This is an exit-liquidity test: a position you cannot leave is unsized."""
    assert _scout([_row("THIN", 50.0, 47.0, 100)]).scan(day=date(2026, 9, 11)) == []


def test_already_exploded_names_are_excluded():
    """A name up 60% has had its move; entering after it is chasing."""
    assert _scout([_row("MOON", 160.0, 100.0, 5_000_000)]).scan(day=date(2026, 9, 11)) == []


def test_flat_names_are_excluded():
    assert _scout([_row("FLAT", 100.0, 100.1, 5_000_000)]).scan(day=date(2026, 9, 11)) == []


def test_non_common_tickers_are_excluded():
    """Units, warrants and preferreds are thin and not what we screen for."""
    rows = [_row("ABCD.W", 50.0, 47.0, 5_000_000), _row("TOOLONGX", 50.0, 47.0, 5_000_000)]
    assert _scout(rows).scan(day=date(2026, 9, 11)) == []


def test_volume_outranks_a_bigger_move():
    """A big move on thin volume is one motivated buyer and a wide spread."""
    rows = [
        _row("LOUD", 110.0, 100.0, 200_000),      # +10%, $22M
        _row("HEAVY", 103.0, 100.0, 50_000_000),  # +3%, $5.1B
    ]
    assert _scout(rows).scan(day=date(2026, 9, 11))[0].symbol == "HEAVY"


def test_limit_is_respected():
    # Alpha-only tickers: digits are filtered out as non-common shares.
    rows = [_row(f"AA{c}", 100.0, 95.0, 5_000_000) for c in "ABCDEFGHIJKLMNOPQRST"]
    assert len(_scout(rows).scan(day=date(2026, 9, 11), limit=3)) == 3


def test_exclusions_are_honoured():
    rows = [_row("AAPL", 100.0, 95.0, 5_000_000), _row("MSFT", 100.0, 95.0, 5_000_000)]
    out = _scout(rows, exclude=("AAPL",)).scan(day=date(2026, 9, 11))
    assert [c.symbol for c in out] == ["MSFT"]


def test_weekend_resolves_back_to_a_weekday():
    """The grouped endpoint has no weekend session; an empty set would read as
    'no opportunities' rather than 'market was closed'."""
    assert MarketScout._resolve_day(date(2026, 9, 19)).weekday() == 4   # Saturday -> Friday
    assert MarketScout._resolve_day(date(2026, 9, 20)).weekday() == 4   # Sunday -> Friday


def test_no_api_key_returns_nothing_rather_than_raising():
    assert MarketScout(api_key=None).scan(day=date(2026, 9, 11)) == []


def test_fetch_failure_degrades():
    def boom(url, key): raise RuntimeError("503")
    assert MarketScout(api_key="k", fetch=boom).scan(day=date(2026, 9, 11)) == []


def test_malformed_rows_are_skipped():
    rows = [{"T": "BAD"}, {"c": 1}, _row("GOOD", 100.0, 95.0, 5_000_000)]
    assert [c.symbol for c in _scout(rows).scan(day=date(2026, 9, 11))] == ["GOOD"]


def test_zero_open_does_not_divide_by_zero():
    assert _scout([_row("ZERO", 100.0, 0, 5_000_000)]).scan(day=date(2026, 9, 11)) == []


def test_results_are_cached_per_day():
    calls = []

    def counting(url, key):
        calls.append(url)
        return {"results": [_row("AAPL", 100.0, 95.0, 5_000_000)]}

    s = MarketScout(api_key="k", fetch=counting)
    s.scan(day=date(2026, 9, 11))
    s.scan(day=date(2026, 9, 11))
    assert len(calls) == 1
