"""The calibration loop, connected end to end.

It was open at both ends: PositionBook wrote outcomes with NULL confidence, so
fit_confidence_shrink discarded every row at any sample size; and the fitted
value was computed for display while ThesisPipeline used the constant. The
dashboard reported "(fitted)" for a number that governed nothing.

- Blind: an unusable fit must leave the pessimistic constant in force. Sizing
  may never be loosened by a failed or thin fit.
- Blind: the dashboard must read the shrink off the pipeline, not re-derive it.
"""

import pytest

from dashboard.runtime import DashboardRuntime
from finance.exits import ExitPlan
from memory.store import MemoryStore
from roundtable.calibration import fit_confidence_shrink
from trading.pipeline import CONFIDENCE_SHRINK
from trading.position_book import PositionBook


def _book(tmp_path):
    memory = MemoryStore(db_path=str(tmp_path / "loop.db"))
    return PositionBook(memory=memory), memory


def _open_and_close(book, symbol, *, confidence, exit_price, entry=100.0):
    book.open(symbol=symbol, asset_class="equity", quantity=1.0, entry_price=entry,
              plan=ExitPlan(entry=entry, stop=entry - 5, target=entry + 10,
                            direction="long", atr=2.0),
              thesis_id=f"t-{symbol}", signal="bullish", confidence=confidence)
    return book.close(symbol, exit_price, reason="target")


def test_the_outcome_carries_the_confidence_that_was_staked(tmp_path):
    book, memory = _book(tmp_path)
    _open_and_close(book, "AAPL", confidence=80.0, exit_price=110.0)
    row = memory.get_thesis_outcome("t-AAPL")
    assert row["confidence"] == 80.0 and row["signal"] == "bullish"
    assert row["correct"] == 1


def test_a_fit_becomes_possible_at_all(tmp_path):
    """Previously zero rows survived the filter at ANY n."""
    book, memory = _book(tmp_path)
    for i in range(35):
        _open_and_close(book, f"S{i}", confidence=80.0,
                        exit_price=110.0 if i % 3 else 90.0)
    fit = fit_confidence_shrink(memory.resolved_outcomes(limit=500))
    assert fit.samples == 35, "every row used to be discarded for a NULL confidence"
    assert fit.usable and 0.1 <= fit.shrink <= 0.9


def test_a_thin_sample_leaves_the_pessimistic_constant_in_force(tmp_path):
    book, memory = _book(tmp_path)
    _open_and_close(book, "AAPL", confidence=80.0, exit_price=110.0)
    fit = fit_confidence_shrink(memory.resolved_outcomes(limit=500))
    assert not fit.usable
    # What build_fund does with an unusable fit.
    assert (fit.shrink if fit.usable else CONFIDENCE_SHRINK) == CONFIDENCE_SHRINK


def test_a_failing_fit_never_loosens_sizing(tmp_path):
    from dashboard.fund_wiring import _fit_shrink

    class Broken:
        def resolved_outcomes(self, limit): raise RuntimeError("db gone")
    fit = _fit_shrink(Broken())
    assert not fit.usable and fit.shrink is None


def test_the_dashboard_reports_the_shrink_the_pipeline_actually_uses(tmp_path):
    """Not the one it could re-derive — those are what drifted apart."""
    class Pipeline: confidence_shrink = 0.42
    class Fund: pipeline = Pipeline()
    class Sched: fund = Fund()
    rt = DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "d.db")),
                          fund_scheduler=Sched())
    applied = rt._enforcement_for(None, 0.42)["applied"][0]
    assert "0.42" in applied and "fitted from resolved outcomes" in applied

    # A fit that exists but is NOT the one in force must not be called fitted.
    stale = rt._enforcement_for(None, 0.77)["applied"][0]
    assert "0.42" in stale and "pessimistic default" in stale
