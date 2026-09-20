"""The public read surface, and the two API shapes that bite.

No network: every test injects `fetch`. A test suite that reaches the live
exchange fails on a weekend and passes on a Tuesday, which teaches you to
ignore it.
"""

from __future__ import annotations

import pytest

from trading.kalshi.rest import (
    KalshiPublic,
    Window,
    candle_quote,
    dollars,
    parse_ts,
    round_to_tick,
    tick_size,
)


# ---------- the tapered tick ladder ----------

@pytest.mark.parametrize("price, expected", [
    (0.001, 0.001), (0.05, 0.001), (0.099, 0.001),   # deci-cent tail
    (0.10, 0.01), (0.50, 0.01), (0.90, 0.01),        # cent middle
    (0.901, 0.001), (0.95, 0.001), (0.999, 0.001),   # deci-cent tail
])
def test_ticks_taper_at_the_tails(price, expected):
    """tapered_deci_cent: the tails are priced ten times more finely.

    Which is also where the fee is smallest, so it is the part of the curve any
    strategy here would live on.
    """
    assert tick_size(price) == expected


def test_rounding_never_improves_our_price():
    """Up for an offer, down for a bid — always away from us.

    Rounding the convenient way would quietly cross further than intended and
    make a backtest fill at prices the exchange would not have given.
    """
    assert round_to_tick(0.523, up=True) == 0.53
    assert round_to_tick(0.523, up=False) == 0.52
    assert round_to_tick(0.0704, up=True) == 0.071
    assert round_to_tick(0.0704, up=False) == 0.07


def test_a_price_already_on_the_grid_does_not_move():
    """0.07/0.001 is 69.99999999999999 in binary float; a naive floor loses a tick."""
    for p in (0.07, 0.05, 0.5, 0.42, 0.95):
        assert round_to_tick(p, up=True) == pytest.approx(p)
        assert round_to_tick(p, up=False) == pytest.approx(p)


# ---------- money as strings ----------

@pytest.mark.parametrize("raw, expected", [
    ("0.6400", 0.64), ("0.0010", 0.001), ("1.0000", 1.0), ("0.0000", 0.0),
])
def test_decimal_strings_parse_to_dollars(raw, expected):
    assert dollars(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", None, "abc", "1.5", "-0.2"])
def test_a_missing_or_impossible_price_is_none_not_zero(raw):
    """No bid is not a bid at zero. Treating it as one invents a free contract."""
    assert dollars(raw) is None


def test_candle_quote_reads_the_close_not_the_mean():
    """The mean is an average over a minute we could not have traded at."""
    candle = {
        "yes_bid": {"close_dollars": "0.4800", "mean_dollars": "0.6839"},
        "yes_ask": {"close_dollars": "0.4900", "mean_dollars": "0.6900"},
    }
    assert candle_quote(candle) == (pytest.approx(0.48), pytest.approx(0.49))


def test_candle_quote_survives_a_one_sided_book():
    assert candle_quote({"yes_bid": {"close_dollars": "0.48"}}) == (pytest.approx(0.48), None)
    assert candle_quote({}) == (None, None)


def test_parse_ts_handles_kalshis_rfc3339():
    assert parse_ts("2026-09-20T17:30:00Z") == 1789925400.0
    assert parse_ts("") is None and parse_ts(None) is None and parse_ts("nope") is None


# ---------- the client ----------

def _client(responses: dict) -> KalshiPublic:
    client = KalshiPublic(fetch=lambda url: _match(responses, url))
    client._throttle = lambda: None          # type: ignore[method-assign]
    return client


def _match(responses: dict, url: str) -> dict:
    for fragment, payload in responses.items():
        if fragment in url:
            return payload
    raise AssertionError(f"unexpected url {url}")


def test_index_series_is_seconds_and_sorted():
    """The API speaks milliseconds; everything downstream speaks seconds."""
    client = _client({"/live_data/": {"live_data": {"details": {
        "maturity_ts_ms": 1789926300000,
        "timeseries": [
            {"t": 1789922044000, "v": 81236.3},
            {"t": 1789922042000, "v": 81234.4},     # deliberately out of order
            {"t": 1789922043000, "v": 81235.4},
        ],
    }}}})
    series, maturity = client.index_series("KXBTC15M-26SEP201345")
    assert [t for t, _ in series] == [1789922042.0, 1789922043.0, 1789922044.0]
    assert maturity == 1789926300.0


def test_index_series_drops_malformed_points():
    client = _client({"/live_data/": {"live_data": {"details": {"timeseries": [
        {"t": 1789922042000, "v": 81234.4},
        {"t": None, "v": 1.0},
        {"t": 1789922043000},
    ]}}}})
    series, maturity = client.index_series("E")
    assert len(series) == 1 and maturity is None


def test_a_read_failure_is_missing_data_not_a_crash():
    """Callers treat None as 'skip this window' — the right answer for a feed gap."""
    def boom(url: str) -> dict:
        raise ConnectionError("network down")
    client = KalshiPublic(fetch=boom)
    client._throttle = lambda: None          # type: ignore[method-assign]
    assert client.markets() == ([], None)
    assert client.index_series("E") == ([], None)
    assert client.candlesticks("T", start_ts=0, end_ts=1) == []


def test_window_assembles_market_index_and_book():
    client = _client({
        "/live_data/": {"live_data": {"details": {"timeseries": [
            {"t": 1789925400000, "v": 81180.0}, {"t": 1789925401000, "v": 81181.0}]}}},
        "/candlesticks": {"candlesticks": [{"end_period_ts": 1789925460}]},
    })
    window = client.window({
        "ticker": "KXBTC15M-26SEP201330-30",
        "event_ticker": "KXBTC15M-26SEP201330",
        "open_time": "2026-09-20T17:15:00Z",
        "close_time": "2026-09-20T17:30:00Z",
        "floor_strike": 81180.59,
        "result": "no",
    })
    assert window is not None
    assert window.settled and window.floor_strike == pytest.approx(81180.59)
    assert len(window.index) == 2 and len(window.book) == 1


@pytest.mark.parametrize("missing", ["ticker", "event_ticker", "open_time", "close_time"])
def test_a_market_missing_its_identity_yields_no_window(missing):
    client = _client({})
    market = {
        "ticker": "T", "event_ticker": "E",
        "open_time": "2026-09-20T17:15:00Z", "close_time": "2026-09-20T17:30:00Z",
    }
    del market[missing]
    assert client.window(market) is None


# ---------- Window arithmetic ----------

def _window(**kw) -> Window:
    base = dict(ticker="T", event_ticker="E", open_ts=0.0, close_ts=900.0,
                floor_strike=81_000.0, result="yes",
                index=tuple((float(t), 81_000.0 + t) for t in range(0, 901)))
    base.update(kw)
    return Window(**base)                     # type: ignore[arg-type]


def test_index_at_never_looks_ahead():
    """The single easiest way to backtest a profit that does not exist."""
    w = _window()
    assert w.index_at(500.0) == pytest.approx(81_500.0)
    assert w.index_at(500.9) == pytest.approx(81_500.0)   # not 81_501
    assert w.index_at(-1.0) is None


def test_index_mean_is_half_open():
    w = _window()
    assert w.index_mean(840.0, 900.0) == pytest.approx(
        sum(81_000.0 + t for t in range(840, 900)) / 60.0)


def test_index_mean_of_a_gap_is_unknown():
    assert _window(index=()).index_mean(0.0, 900.0) is None


def test_tau_never_goes_negative():
    w = _window()
    assert w.tau_at(0.0) == 900.0
    assert w.tau_at(900.0) == 0.0
    assert w.tau_at(1000.0) == 0.0


@pytest.mark.parametrize("result, settled", [("yes", True), ("no", True), ("", False)])
def test_settled_means_the_exchange_has_ruled(result, settled):
    assert _window(result=result).settled is settled
