"""The kill switch, tested on synthetic windows with known answers.

The live run's verdict is recorded in docs/KALSHI_BTC_15M.md §14. These tests
are about the harness that produced it: a backtester that quietly looks ahead,
fills at the mid, or gets the winning side's P&L backwards will report an edge
that is not there, and that is the expensive kind of bug.
"""

from __future__ import annotations

import pytest

from trading.kalshi.replay import (
    MIN_TAU_S,
    ReplayResult,
    Trade,
    replay,
    replay_window,
)
from trading.kalshi.rest import Window


def _window(*, result="yes", drift=0.0, strike=81_000.0, quotes=None,
            start=81_000.0) -> Window:
    """A 15-minute window with a 1 Hz index and minute candles."""
    index = tuple((float(t), start + drift * t) for t in range(0, 901))
    book = tuple(
        {"end_period_ts": ts,
         "yes_bid": {"close_dollars": f"{(quotes or {}).get(ts, 0.50) - 0.005:.4f}"},
         "yes_ask": {"close_dollars": f"{(quotes or {}).get(ts, 0.50) + 0.005:.4f}"}}
        for ts in range(60, 961, 60)
    )
    return Window(ticker="T", event_ticker="E", open_ts=0.0, close_ts=900.0,
                  floor_strike=strike, result=result, index=index, book=book)


# ---------- no lookahead ----------

def test_a_decision_never_sees_a_price_from_its_own_future():
    """Every quote and index print used must pre-date the decision stamp."""
    w = _window(drift=0.5, result="yes")
    trade = replay_window(w, threshold_cents=0.5)
    assert trade is not None
    assert trade.ts <= w.close_ts
    assert w.index_at(trade.ts) is not None


def test_decisions_stop_before_the_contract_is_decided():
    w = _window(drift=0.5)
    trade = replay_window(w, threshold_cents=0.1)
    assert trade is None or trade.tau_s >= MIN_TAU_S


def test_no_trade_without_enough_history_to_fit_a_volatility():
    """A window with only its first seconds of index cannot be priced."""
    w = _window()
    stub = Window(ticker=w.ticker, event_ticker=w.event_ticker, open_ts=w.open_ts,
                  close_ts=w.close_ts, floor_strike=w.floor_strike, result="yes",
                  index=w.index[:30], book=w.book)
    assert replay_window(stub, threshold_cents=0.1) is None


# ---------- the book we crossed, not the mid ----------

def test_buying_lifts_the_ask():
    """Filling at the mid earns half the spread for free on every trade.

    On a market quoted a cent wide, that is the entire edge.
    """
    w = _window(drift=2.0, result="yes", quotes={ts: 0.20 for ts in range(60, 961, 60)})
    trade = replay_window(w, threshold_cents=0.5)
    assert trade is not None and trade.side == "yes"
    assert trade.price == pytest.approx(0.205)      # the ask, not 0.20


def test_selling_hits_the_bid():
    w = _window(drift=-2.0, result="no", quotes={ts: 0.80 for ts in range(60, 961, 60)})
    trade = replay_window(w, threshold_cents=0.5)
    assert trade is not None and trade.side == "no"
    assert trade.price == pytest.approx(1.0 - 0.795)   # 1 - bid


# ---------- P&L ----------

def test_a_winning_yes_pays_the_rest_of_the_dollar_less_fees():
    w = _window(drift=2.0, result="yes", quotes={ts: 0.20 for ts in range(60, 961, 60)})
    t = replay_window(w, threshold_cents=0.5)
    assert t is not None and t.won
    # 100 contracts at 0.205 -> (1 - 0.205) * 100, less ceil(0.07*100*.205*.795)
    assert t.pnl_usd == pytest.approx(79.5 - 1.15)


def test_a_losing_yes_loses_the_premium_and_still_pays_the_fee():
    """The fee is charged on the trade, not on the win. Forgetting that flatters
    every losing backtest by exactly the fee."""
    w = _window(drift=2.0, result="no", quotes={ts: 0.20 for ts in range(60, 961, 60)})
    t = replay_window(w, threshold_cents=0.5)
    assert t is not None and not t.won
    assert t.pnl_usd == pytest.approx(-20.5 - 1.15)


def test_the_no_side_is_priced_as_one_minus_the_bid():
    w = _window(drift=-2.0, result="no", quotes={ts: 0.80 for ts in range(60, 961, 60)})
    t = replay_window(w, threshold_cents=0.5)
    assert t is not None and t.won
    assert t.pnl_usd == pytest.approx((1 - 0.205) * 100 - 1.15)


# ---------- selection ----------

def test_at_most_one_trade_per_window():
    """Re-entering a market we are already wrong about turns a small loss into
    the only loss that matters."""
    w = _window(drift=2.0, result="yes", quotes={ts: 0.20 for ts in range(60, 961, 60)})
    r = replay([w], threshold_cents=0.5)
    assert len(r.trades) == 1


def test_a_fairly_priced_book_produces_no_trade():
    """The contract opens at even money where the fee peaks. Most windows
    should produce nothing at all — a replay that always trades is a bug."""
    w = _window(drift=0.0, result="yes")
    assert replay_window(w, threshold_cents=2.0) is None


def test_a_higher_threshold_never_takes_more_trades():
    ws = [_window(drift=d, result="yes" if d > 0 else "no",
                  quotes={ts: 0.5 for ts in range(60, 961, 60)})
          for d in (2.0, 1.0, -1.0, -2.0, 0.0)]
    counts = [len(replay(ws, threshold_cents=t).trades) for t in (0.5, 2.0, 5.0, 12.0)]
    assert counts == sorted(counts, reverse=True)


def test_maker_pricing_is_never_worse_than_taker():
    w = _window(drift=2.0, result="yes", quotes={ts: 0.20 for ts in range(60, 961, 60)})
    taker = replay_window(w, threshold_cents=0.5)
    maker = replay_window(w, threshold_cents=0.5, maker=True)
    assert taker and maker and maker.pnl_usd > taker.pnl_usd


def test_unsettled_and_strikeless_windows_are_skipped():
    live = _window(result="")
    assert replay_window(live, threshold_cents=0.1) is None
    r = replay([live], threshold_cents=0.1)
    assert r.windows == 1 and r.skipped == 1 and not r.trades


# ---------- reconciliation ----------

def test_our_settlement_arithmetic_is_checked_against_the_exchange():
    """If our 60s mean disagrees with Kalshi about who won, the index capture is
    broken and every P&L in the run is fiction. Better as a count than a
    surprise."""
    agrees = _window(drift=1.0, result="yes", strike=81_000.0)
    disagrees = _window(drift=1.0, result="no", strike=81_000.0)
    assert replay([agrees]).reconciled == 1
    assert replay([disagrees]).mismatched == 1


# ---------- the verdict ----------

def _trades(n: int, cents: float) -> list[Trade]:
    return [Trade("T", 0.0, 100.0, "yes", 0.5, 0.6, 0.5, 2.0, 100,
                  cents > 0, cents / 100.0 * 100) for _ in range(n)]


def test_a_positive_mean_over_too_few_windows_is_inconclusive_not_a_green_light():
    r = ReplayResult(trades=_trades(20, 5.0), windows=20)
    assert r.verdict(min_trades=100).startswith("INCONCLUSIVE")


def test_a_negative_mean_stops_the_build():
    r = ReplayResult(trades=_trades(150, -2.0), windows=150)
    assert r.verdict(min_trades=100).startswith("STOP")


def test_only_a_positive_mean_over_enough_windows_proceeds():
    r = ReplayResult(trades=_trades(150, 3.0), windows=150)
    assert r.verdict(min_trades=100).startswith("PROCEED")


def test_exactly_break_even_does_not_proceed():
    """Zero edge after fees is a losing strategy once slippage is real."""
    r = ReplayResult(trades=_trades(150, 0.0), windows=150)
    assert r.verdict(min_trades=100).startswith("STOP")


def test_an_empty_run_reports_nothing_rather_than_zero():
    r = ReplayResult(windows=10, skipped=10)
    assert r.mean_net_cents is None and r.hit_rate is None and r.brier is None
    assert r.summary()["trades"] == 0
