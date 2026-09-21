"""Catalyst evidence: dated events and insider flow, computed before any seat sees it.

The fund reasoned from price, fundamentals and scraped sentiment. None of those
see the thing that most often moves a name on a given day: a scheduled event, or
the people who run the company buying or selling it.

Two rules from the existing codebase carry over unchanged, because breaking
either is how this turns into noise:

- **Computed, not narrated.** `candidate_builder` computes every number before
  the table convenes. Handing six seats twenty raw Form 4 rows would have each
  of them do arithmetic badly and differently. The net flow is derived here.
- **Narrative never becomes a size.** Headlines are context. No figure lifted
  from a news body reaches the sizer, exactly as `roundtable.corroboration`
  refuses a scraped number.

Degrading is a first-class path, not an error case: OpenBB is optional, most of
its calendars need an API key, and the fund must trade identically without it.
"""

import asyncio

import pytest

from trading.catalysts import (
    CatalystEvidence, CatalystFeed, summarise_insiders, summarise_news,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- degrading

def test_no_openbb_installed_is_silence_not_a_crash():
    """The fund traded before this existed and must trade identically without
    it. An import error here cannot be allowed to end a cycle."""
    feed = CatalystFeed(_import=lambda: (_ for _ in ()).throw(ImportError("no openbb")))
    ev = _run(feed.catalysts("AAPL", "equity"))
    assert ev.notes == () and ev.available is False
    assert "not installed" in ev.reason.lower()


def test_one_failing_endpoint_does_not_lose_the_others():
    """Most OpenBB calendars need a paid key. Losing the whole block because
    one of five is gated would mean the keyless install shows nothing."""
    class _Obb:
        class news:
            @staticmethod
            def company(**kw):
                return _Res([{"date": "2026-09-20", "title": "Real headline", "source": "x"}])
        class equity:
            class ownership:
                @staticmethod
                def insider_trading(**kw):
                    raise RuntimeError("needs an api key")

    ev = _run(CatalystFeed(_import=lambda: _Obb).catalysts("AAPL", "equity"))
    assert any("Real headline" in n for n in ev.notes)
    assert ev.available is True
    assert any("insider" in d.lower() for d in ev.degraded)


def test_a_slow_provider_releases_the_cycle_on_time():
    """A cycle is 300s and this is one of several network calls in it.

    Measured INSIDE the event loop on purpose. `asyncio.wait_for` unblocks the
    await; it cannot kill the worker thread, which runs to completion in the
    background — Python has no thread cancellation. What matters is that the
    fund stops waiting, which is what this asserts. `asyncio.run` itself would
    still block at shutdown joining that thread, so timing the whole call would
    measure the executor, not the guarantee.
    """
    import time

    class _Obb:
        class news:
            @staticmethod
            def company(**kw):
                time.sleep(5)
                return _Res([])
        class equity:
            class ownership:
                @staticmethod
                def insider_trading(**kw): return _Res([])

    async def _timed():
        feed = CatalystFeed(_import=lambda: _Obb, timeout_seconds=0.2)
        started = time.monotonic()
        ev = await feed.catalysts("AAPL", "equity")
        return time.monotonic() - started, ev

    elapsed, ev = _run(_timed())
    assert elapsed < 2.0, f"the cycle waited {elapsed:.1f}s on a timed-out provider"
    assert any("timed out" in d.lower() for d in ev.degraded)


# ---------------------------------------------------------------- insiders

def test_insider_flow_is_computed_not_handed_over_raw():
    """Six seats doing Form 4 arithmetic independently is six chances to get it
    wrong. One derived line, computed once."""
    rows = [
        {"owner_name": "A", "officer": True, "acquisition_or_disposition": "A",
         "securities_transacted": 1000, "transaction_price": 10.0, "transaction_date": "2026-09-15"},
        {"owner_name": "B", "officer": True, "acquisition_or_disposition": "A",
         "securities_transacted": 500, "transaction_price": 10.0, "transaction_date": "2026-09-16"},
    ]
    note = summarise_insiders(rows)
    assert "2 insider" in note and "bought" in note
    assert "$15,000" in note


def test_selling_and_buying_net_against_each_other():
    rows = [
        {"owner_name": "A", "acquisition_or_disposition": "A",
         "securities_transacted": 1000, "transaction_price": 10.0},
        {"owner_name": "B", "acquisition_or_disposition": "D",
         "securities_transacted": 3000, "transaction_price": 10.0},
    ]
    assert "sold" in summarise_insiders(rows)


def test_routine_scheduled_selling_is_labelled_as_such():
    """A 10b5-1 plan sale is a calendar entry, not a signal. Reporting it as
    conviction is how an insider panel starts lying."""
    rows = [{"owner_name": "A", "acquisition_or_disposition": "D",
             "securities_transacted": 1000, "transaction_price": 10.0,
             "footnote": "This transaction was made pursuant to a Rule 10b5-1 trading plan."}]
    assert "10b5-1" in summarise_insiders(rows)


def test_no_filings_says_so_rather_than_implying_no_activity():
    assert "no form 4" in summarise_insiders([]).lower()


def test_a_row_missing_its_price_is_skipped_not_guessed():
    rows = [{"owner_name": "A", "acquisition_or_disposition": "A",
             "securities_transacted": 1000, "transaction_price": None}]
    assert "no form 4" in summarise_insiders(rows).lower()


# ---------------------------------------------------------------- news

def test_headlines_carry_their_date_and_source():
    """An undated headline is unusable: the seats cannot tell last year's
    story from this morning's."""
    note = summarise_news([{"date": "2026-09-20T23:39:48+00:00",
                            "title": "Foldable iPhone", "source": "Barchart"}])
    assert "2026-09-20" in note[0] and "Barchart" in note[0]


def test_news_bodies_are_not_forwarded():
    """Only the headline. A full article body is thousands of tokens per
    candidate and is where a seat finds a number nobody verified."""
    note = summarise_news([{"date": "2026-09-20", "title": "T", "source": "s",
                            "body": "SECRET BODY " * 200}])
    assert not any("SECRET BODY" in n for n in note)


def test_headline_count_is_capped():
    rows = [{"date": "2026-09-20", "title": f"H{i}", "source": "s"} for i in range(50)]
    assert len(summarise_news(rows)) <= 8


class _Res:
    def __init__(self, rows): self._rows = rows
    @property
    def results(self):
        return [_Row(r) for r in self._rows]


class _Row:
    def __init__(self, d): self._d = d
    def model_dump(self): return dict(self._d)


# ---------------------------------------------------------------- dashboard

def test_the_dashboard_never_calls_the_provider_at_poll_rate(tmp_path):
    """The UI polls at 1Hz. yfinance and SEC EDGAR are rate-limited and these
    calls take seconds. Without a cache the panel would be a self-inflicted
    denial of service against the sources the fund depends on."""
    from dashboard.runtime import DashboardRuntime
    from memory.store import MemoryStore

    calls = []

    class _Feed:
        async def catalysts(self, symbol, asset_class="equity"):
            calls.append(symbol)
            return CatalystEvidence(notes=("[2026-09-20] x (s)",), available=True)

    rt = DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "c.db")),
                          catalyst_feed=_Feed())
    for _ in range(50):
        _run(rt.catalysts("AAPL"))
    assert len(calls) == 1, f"hit the provider {len(calls)} times for 50 polls"


def test_an_unavailable_feed_still_answers_the_route(tmp_path):
    """The panel must render a reason, not a 500."""
    from dashboard.runtime import DashboardRuntime
    from memory.store import MemoryStore

    class _Broken:
        async def catalysts(self, symbol, asset_class="equity"):
            raise RuntimeError("provider exploded")

    rt = DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "c.db")),
                          catalyst_feed=_Broken())
    out = _run(rt.catalysts("AAPL"))
    assert out["available"] is False and out["reason"]


# ---------------------------------------------------------------- earnings

def test_an_earnings_date_inside_the_window_is_a_dated_warning():
    """The single most decision-relevant catalyst a swing fund has. A
    technically perfect setup entered before a print is a coin flip, and this
    is the only line on the page that says so."""
    from trading.catalysts import summarise_earnings
    rows = [{"symbol": "COST", "report": {"date": "2026-09-24", "timing": "pm",
                                          "verified": True},
             "eps": {"estimate": "6.52", "actual": None}}]
    note = summarise_earnings(rows, "COST", today="2026-09-21")
    assert "3 days" in note and "2026-09-24" in note and "after the close" in note


def test_an_unverified_date_is_called_tentative():
    """Robinhood flags unverified dates. Presenting a guess as a fact is how a
    seat vetoes a good trade for nothing."""
    from trading.catalysts import summarise_earnings
    rows = [{"symbol": "X", "report": {"date": "2026-09-24", "timing": "am",
                                       "verified": False}, "eps": {}}]
    assert "tentative" in summarise_earnings(rows, "X", today="2026-09-21").lower()


def test_already_reported_is_not_an_upcoming_event():
    """`eps.actual` populated means it has happened. Reading a past print as
    an upcoming one inverts the advice."""
    from trading.catalysts import summarise_earnings
    rows = [{"symbol": "X", "report": {"date": "2026-09-19", "timing": "am",
                                       "verified": True},
             "eps": {"estimate": "1.0", "actual": "1.2"}}]
    note = summarise_earnings(rows, "X", today="2026-09-21")
    assert "reported" in note.lower() and "beat" in note.lower()


def test_a_quiet_window_says_so_explicitly():
    """Silence and 'we did not look' must not render identically."""
    from trading.catalysts import summarise_earnings
    assert "no earnings" in summarise_earnings([], "AAPL", today="2026-09-21").lower()


def test_another_companys_earnings_are_not_this_symbols():
    from trading.catalysts import summarise_earnings
    rows = [{"symbol": "COST", "report": {"date": "2026-09-24", "verified": True},
             "eps": {}}]
    assert "no earnings" in summarise_earnings(rows, "AAPL", today="2026-09-21").lower()


# ---------------------------------------------------------------- implied move

def test_the_implied_move_is_compared_against_our_own_stop():
    """The whole point. "Earnings Thursday" is trivia; "the options price a
    move twice your stop distance" is a decision.

    This is the one number that connects the catalyst block to the exit plan,
    and it is why the options call is worth making at all."""
    from trading.catalysts import summarise_implied_move
    note = summarise_implied_move(implied_move_pct=6.8, stop_distance_pct=4.1,
                                  expiry="2026-09-25", symbol="COST")
    assert "6.8%" in note and "4.1%" in note
    assert "wider than" in note.lower() or "exceeds" in note.lower()


def test_a_stop_that_survives_the_print_is_said_to_survive_it():
    from trading.catalysts import summarise_implied_move
    note = summarise_implied_move(implied_move_pct=2.0, stop_distance_pct=5.0,
                                  expiry="2026-09-25", symbol="X")
    assert "survive" in note.lower()


def test_no_stop_means_no_comparison_rather_than_a_guessed_one():
    """A candidate with no exit plan is pre-screened out anyway; inventing a
    stop distance to make the sentence work would be a fabricated number in an
    evidence block."""
    from trading.catalysts import summarise_implied_move
    note = summarise_implied_move(implied_move_pct=6.8, stop_distance_pct=None,
                                  expiry="2026-09-25", symbol="X")
    assert "6.8%" in note and "stop" not in note.split(".")[0].lower()


# ---------------------------------------------------------------- filings

def test_a_recent_8k_is_a_dated_material_event():
    from trading.catalysts import summarise_filings
    rows = [{"form_type": "8-K", "description": "Current report",
             "date_filed": "2026-09-19"}]
    note = summarise_filings(rows, today="2026-09-21")
    assert "8-K" in note and "2 days ago" in note


def test_an_old_filing_is_not_presented_as_news():
    from trading.catalysts import summarise_filings
    rows = [{"form_type": "10-K", "date_filed": "2025-01-05"}]
    assert "no material filings" in summarise_filings(rows, today="2026-09-21").lower()


def test_no_filings_is_stated_as_a_checked_absence():
    from trading.catalysts import summarise_filings
    assert "no material filings" in summarise_filings([], today="2026-09-21").lower()


# ---------------------------------------------------------------- depth

def test_a_closed_market_is_not_reported_as_no_liquidity():
    """Robinhood returns an empty book when the market is shut. Rendering that
    as "no resting liquidity" would tell the Risk seat the name is untradeable
    every weekend — and the fund trades crypto at weekends, so a seat primed
    that way is primed at exactly the wrong time."""
    from trading.catalysts import summarise_depth
    note = summarise_depth({"bids": [], "asks": []}, equities_open=False)
    assert "closed" in note.lower() and "no liquidity" not in note.lower()


def test_an_empty_book_in_open_hours_is_a_real_warning():
    from trading.catalysts import summarise_depth
    note = summarise_depth({"bids": [], "asks": []}, equities_open=True)
    assert "no resting" in note.lower()


def test_a_wall_is_called_out_for_stop_placement():
    """The real use at this account size. $150 does not move a book, but a
    stop sitting just below a large resting bid is in a different place from
    one sitting in thin air."""
    from trading.catalysts import summarise_depth
    book = {"bids": [{"price": "100.00", "quantity": 100},
                     {"price": "99.90", "quantity": 12000},
                     {"price": "99.80", "quantity": 150}],
            "asks": [{"price": "100.10", "quantity": 120}]}
    note = summarise_depth(book, equities_open=True)
    assert "99.9" in note and "wall" in note.lower()


def test_an_even_book_reports_no_wall():
    from trading.catalysts import summarise_depth
    book = {"bids": [{"price": "100.0", "quantity": 100},
                     {"price": "99.9", "quantity": 110}],
            "asks": [{"price": "100.1", "quantity": 105}]}
    assert "no outsized" in summarise_depth(book, equities_open=True).lower()


def test_the_stop_distance_handed_to_the_options_line_is_the_real_geometry():
    """If this drifted from `finance.exits`, the implied-move comparison would
    read as a real one while comparing against a stop the fund never places."""
    from finance.exits import DEFAULT_STOP_MULTIPLIER, average_true_range
    from trading.fund import _stop_distance_pct

    class _Bar:
        def __init__(s, h, l, c): s.high, s.low, s.close, s.open = h, l, c, c

    bars = [_Bar(101 + i * 0.1, 99 + i * 0.1, 100 + i * 0.1) for i in range(30)]
    expected = average_true_range(bars) * DEFAULT_STOP_MULTIPLIER / 100.0 * 100.0
    assert _stop_distance_pct(bars, 100.0) == round(expected, 2)


def test_bars_too_thin_for_an_atr_yield_no_stop_distance():
    from trading.fund import _stop_distance_pct
    assert _stop_distance_pct([], 100.0) is None
    assert _stop_distance_pct([object()], 0.0) is None
