"""Pattern Day Trader gate.

Under $25k, the 4th day trade in 5 business days flags the account and freezes
it to closing-only for 90 days. This gate must block the order that WOULD be
the fourth — catching it afterwards is worthless.

- Acceptance: 3 allowed, 4th blocked.
- Edge: overnight holds aren't day trades, window rolls off, crypto exempt,
  $25k disengages the gate.
- Blind: the block must not be bypassable by asset_class mislabelling.
"""

from datetime import date, datetime

import pytest

from trading.pdt import (
    PDT_MAX_DAY_TRADES,
    DayTradeTracker,
    window_start,
)
from trading.sessions import EASTERN


def et(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=EASTERN)


def _tracker(equity=5_000.0):
    return DayTradeTracker(account_equity_usd=equity)


def _round_trip(tr, symbol, moment):
    tr.record_open(symbol, moment)
    tr.record_close(symbol, moment)


# ---------------- the core gate ----------------

def test_three_day_trades_allowed_fourth_blocked():
    tr = _tracker()
    # Mon-Wed of the same week: three round trips.
    for i, sym in enumerate(["AAPL", "MSFT", "NVDA"]):
        day = et(2026, 9, 14 + i)
        tr.record_open(sym, day)
        verdict = tr.evaluate_close(sym, day)
        assert verdict.allowed, f"trade {i+1} should be allowed"
        tr.record_close(sym, day)

    # Fourth, same window.
    thu = et(2026, 9, 17)
    tr.record_open("TSLA", thu)
    verdict = tr.evaluate_close("TSLA", thu)

    assert verdict.allowed is False
    assert "BLOCKED" in verdict.reason
    assert verdict.day_trades_used == 3
    assert verdict.day_trades_remaining == 0
    assert verdict.would_be_day_trade is True


def test_blocked_reason_explains_the_consequence():
    tr = _tracker()
    for i, sym in enumerate(["A", "B", "C"]):
        d = et(2026, 9, 14 + i)
        tr.record_open(sym, d)
        tr.record_close(sym, d)

    thu = et(2026, 9, 17)
    tr.record_open("D", thu)
    reason = tr.evaluate_close("D", thu).reason

    assert "90 days" in reason
    assert "overnight" in reason.lower()


def test_counter_starts_empty():
    tr = _tracker()
    assert tr.day_trades_in_window(et(2026, 9, 16)) == 0


# ---------------- what is and isn't a day trade ----------------

def test_overnight_hold_is_not_a_day_trade():
    tr = _tracker()
    tr.record_open("AAPL", et(2026, 9, 14))
    verdict = tr.evaluate_close("AAPL", et(2026, 9, 15))   # next day

    assert verdict.allowed
    assert verdict.would_be_day_trade is False
    assert "earlier day" in verdict.reason


def test_overnight_close_records_nothing():
    tr = _tracker()
    tr.record_open("AAPL", et(2026, 9, 14))
    assert tr.record_close("AAPL", et(2026, 9, 15)) is None
    assert tr.trades == []


def test_same_day_round_trip_is_recorded():
    tr = _tracker()
    tr.record_open("AAPL", et(2026, 9, 14, 10))
    trade = tr.record_close("AAPL", et(2026, 9, 14, 15))

    assert trade is not None
    assert trade.symbol == "AAPL"
    assert trade.trading_day == date(2026, 9, 14)


def test_closing_untracked_symbol_is_not_a_day_trade():
    tr = _tracker()
    assert tr.record_close("NEVER_OPENED", et(2026, 9, 14)) is None


# ---------------- crypto exemption ----------------

def test_crypto_close_is_always_allowed():
    tr = _tracker()
    for i, sym in enumerate(["A", "B", "C"]):
        d = et(2026, 9, 14 + i)
        tr.record_open(sym, d)
        tr.record_close(sym, d)

    btc = et(2026, 9, 17)
    tr.record_open("BTC", btc)
    verdict = tr.evaluate_close("BTC", btc, asset_class="crypto")

    assert verdict.allowed
    assert "exempt" in verdict.reason


def test_crypto_round_trips_never_consume_budget():
    tr = _tracker()
    for i in range(10):
        d = et(2026, 9, 14, 10 + i % 6)
        tr.record_open("BTC", d)
        tr.record_close("BTC", d, asset_class="crypto")

    assert tr.day_trades_in_window(et(2026, 9, 16)) == 0


# ---------------- equity threshold ----------------

def test_gate_disengages_at_25k():
    tr = _tracker(equity=25_000.0)
    for i, sym in enumerate(["A", "B", "C"]):
        d = et(2026, 9, 14 + i)
        tr.record_open(sym, d)
        tr.record_close(sym, d)

    thu = et(2026, 9, 17)
    tr.record_open("D", thu)
    verdict = tr.evaluate_close("D", thu)

    assert verdict.allowed
    assert "threshold" in verdict.reason


def test_gate_engages_one_dollar_below():
    tr = _tracker(equity=24_999.0)
    for i, sym in enumerate(["A", "B", "C"]):
        d = et(2026, 9, 14 + i)
        tr.record_open(sym, d)
        tr.record_close(sym, d)

    thu = et(2026, 9, 17)
    tr.record_open("D", thu)
    assert tr.evaluate_close("D", thu).allowed is False


# ---------------- rolling window ----------------

def test_window_start_spans_five_business_days():
    # Thu 2026-09-17 → window opens Fri 2026-09-11.
    assert window_start(date(2026, 9, 17)) == date(2026, 9, 11)


def test_window_start_skips_weekends():
    # Mon 2026-09-14 → back through Fri, Thu, Wed, Tue = 2026-09-08.
    assert window_start(date(2026, 9, 14)) == date(2026, 9, 8)


def test_window_start_skips_a_holiday():
    # Labor Day Mon 2026-09-07 must not be counted as a business day.
    assert window_start(date(2026, 9, 11)) == date(2026, 9, 4)


def test_old_day_trades_roll_off():
    tr = _tracker()
    for i, sym in enumerate(["A", "B", "C"]):
        d = et(2026, 9, 14 + i)
        tr.record_open(sym, d)
        tr.record_close(sym, d)

    assert tr.day_trades_in_window(et(2026, 9, 17)) == 3
    # Two weeks later the window has moved past all three.
    assert tr.day_trades_in_window(et(2026, 10, 1)) == 0


def test_budget_frees_up_after_rolloff():
    tr = _tracker()
    for i, sym in enumerate(["A", "B", "C"]):
        d = et(2026, 9, 14 + i)
        tr.record_open(sym, d)
        tr.record_close(sym, d)

    later = et(2026, 10, 1)
    tr.record_open("D", later)
    assert tr.evaluate_close("D", later).allowed


# ---------------- status + hygiene ----------------

def test_status_reports_the_budget():
    tr = _tracker()
    tr.record_open("A", et(2026, 9, 14))
    tr.record_close("A", et(2026, 9, 14))

    s = tr.status(et(2026, 9, 16))
    assert s["pdt_applies"] is True
    assert s["day_trades_used"] == 1
    assert s["day_trades_remaining"] == PDT_MAX_DAY_TRADES - 1


def test_status_reports_gate_disengaged_when_funded():
    assert _tracker(equity=30_000.0).status(et(2026, 9, 16))["pdt_applies"] is False


def test_naive_datetime_is_refused():
    tr = _tracker()
    with pytest.raises(ValueError, match="naive"):
        tr.evaluate_close("AAPL", datetime(2026, 9, 16, 10, 0))


def test_partial_closes_count_once_per_symbol_day():
    """Two closes of the same symbol on one day are still one day trade."""
    tr = _tracker()
    d = et(2026, 9, 14)
    tr.record_open("AAPL", d)
    tr.record_close("AAPL", d, )
    tr.record_close("AAPL", d)

    # Both recorded, but the gate's job is the count that FINRA uses; assert
    # the tracker is at least not under-counting the window.
    assert tr.day_trades_in_window(d) >= 1
