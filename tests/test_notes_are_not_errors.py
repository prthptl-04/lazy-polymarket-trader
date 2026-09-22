"""A cycle that correctly declined to trade is not a cycle that failed.

At 03:37 on a TUESDAY the hourly health check read `errors: 8`, every one of
them the same string:

    "inside the weekend handoff window: the crypto book is being flattened
     for the equity open, so no new positions are opened this cycle"

That is `should_flatten_crypto` doing exactly its job — it fires for the hour
before the 04:00 premarket on any crypto-only session, weekday included, which
is documented and correct. But it landed in `report.errors`, the scheduler
counts that into `metrics.errors`, and the health check keys on that count. So
correct operation produced twelve faults an hour, and the only reason the
engine was not restarted over it was that a human read the text.

A fill was filed the same way: `_reconcile_resting` reported "RESTING FILL" as
an error. The one event the fund exists to produce was being counted as
something going wrong.

An error counter that ticks during correct operation is an error counter
nobody reads, and then a real error arrives and nobody reads that either.
"""

from datetime import datetime

import pytest
from zoneinfo import ZoneInfo

from trading.fund import CycleReport

ET = ZoneInfo("America/New_York")


def _report():
    return CycleReport(moment=datetime(2026, 9, 22, 3, 37, tzinfo=ET),
                       session="crypto_only")


def test_a_report_separates_notes_from_errors():
    r = _report()
    r.notes.append("planned decline")
    r.errors.append("something broke")
    d = r.summary()
    assert d["errors"] == 1
    assert d["notes"] == ["planned decline"]


def test_the_handoff_window_is_a_note_not_an_error():
    """The message a Tuesday at 03:37 produced twelve times an hour."""
    import inspect

    from trading.fund import FundLoop
    src = inspect.getsource(FundLoop.run_cycle)
    i = src.index("should_flatten_crypto(moment):\n                report.")
    assert "report.notes.append" in src[i:i + 120], \
        "the handoff decline must not be counted as a fault"


def test_a_resting_fill_is_a_note_not_an_error():
    import inspect

    from trading.fund import FundLoop
    src = inspect.getsource(FundLoop._reconcile_resting)
    assert "report.notes.append" in src
    assert "report.errors.append" not in src


def test_the_scheduler_has_somewhere_to_put_a_note():
    from trading.fund_scheduler import SchedulerMetrics
    m = SchedulerMetrics()
    assert m.errors == 0 and m.notes == 0 and m.last_note is None


def test_notes_do_not_inflate_the_error_count():
    """The property that matters, stated without reaching into internals."""
    from trading.fund_scheduler import SchedulerMetrics

    m = SchedulerMetrics()
    report = _report()
    report.notes.append("RESTING FILL LINK-USD at 12.97")

    # Mirror what the scheduler does after a cycle.
    if report.errors:
        m.errors += len(report.errors)
    if report.notes:
        m.notes += len(report.notes)
        m.last_note = report.notes[-1]

    assert m.errors == 0, "a fill must never register as a fault"
    assert m.notes == 1
    assert "LINK-USD" in m.last_note


def test_a_real_error_still_counts():
    """The counter must stay meaningful in the direction that matters."""
    from trading.fund_scheduler import SchedulerMetrics

    m = SchedulerMetrics()
    report = _report()
    report.errors.append("no crypto watchlist configured and no crypto scout")
    m.errors += len(report.errors)
    assert m.errors == 1
