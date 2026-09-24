"""The scorecard must find every outcome, not the recent ones.

Measured 2026-09-24. The database held 985 deliberations and 23 resolved
outcomes, and `DashboardRuntime.scorecard()` reported:

    resolved: 0

It joined from the DELIBERATIONS side over a recent-500 window:

    delibs = self.memory.recent_deliberations(limit=500)
    card = score_seats(delibs, {o["thesis_id"]: o for o in outcomes})

708 of those 985 were provider-capped — every seat failed, so none of them has
an outcome and none ever will. They had piled up on top of the scored theses
and pushed all 23 out of the window. `score_seats` skips a deliberation with no
matching outcome, so it found nothing.

The damage is not cosmetic. `monitoring.paper_report` prints that number as
"Resolved theses: 0 / 30" — progress toward the gate that unlocks seat vote
weights, the confidence shrink and post-mortem lessons. A learning counter that
reads zero while the data exists does not delay the unlock, it HIDES it: the
operator reads "no progress" and goes looking for why nothing is being scored,
when scoring is working fine.

Joining from the outcomes side cannot have this failure mode. There are at most
as many deliberations to fetch as there are outcomes, and every one is by
definition the row the outcome points at.
"""

import json

import pytest

from dashboard.runtime import DashboardRuntime


class _Memory:
    """Enough of MemoryStore for the scorecard, with a controllable window."""

    def __init__(self, deliberations, outcomes, window=500):
        self._delibs = deliberations
        self._outcomes = outcomes
        self._window = window
        self.recent_calls = 0

    def recent_deliberations(self, limit=20, status=None):
        self.recent_calls += 1
        return self._delibs[: min(limit, self._window)]

    def resolved_outcomes(self, limit=500):
        return self._outcomes[:limit]

    def get_deliberation(self, thesis_id):
        return next((d for d in self._delibs if d["thesis_id"] == thesis_id), None)


def _delib(tid, signal="bullish", confidence=60.0):
    return {
        "thesis_id": tid,
        "symbol": "AAPL",
        "status": "complete",
        "payload": {
            "consensus": {"signal": signal, "confidence": confidence},
            "opinions": [
                {"seat_id": "quant", "seat_name": "Quantitative Analyst",
                 "signal": signal, "confidence": confidence, "failed": False},
            ],
        },
    }


def _capped(tid):
    """A provider-capped deliberation: every seat failed, no outcome possible."""
    return {
        "thesis_id": tid,
        "symbol": "TSM",
        "status": "complete",
        "payload": {
            "consensus": {"signal": "neutral", "confidence": 0.0},
            "opinions": [
                {"seat_id": "quant", "seat_name": "Quantitative Analyst",
                 "signal": "neutral", "confidence": 0.0, "failed": True},
            ],
        },
    }


def _outcome(tid, realized=0.05):
    return {"thesis_id": tid, "symbol": "AAPL", "signal": "bullish",
            "confidence": 60.0, "realized_return": realized, "correct": realized > 0}


def _runtime(memory):
    r = object.__new__(DashboardRuntime)
    r.memory = memory
    return r


def test_an_outcome_older_than_the_window_is_still_scored():
    """The live failure. Scored theses buried under capped ones."""
    scored = [_delib(f"t{i}") for i in range(5)]
    # 600 capped deliberations pile up on top, more than the 500-row window.
    noise = [_capped(f"c{i}") for i in range(600)]
    memory = _Memory(deliberations=noise + scored,
                     outcomes=[_outcome(f"t{i}") for i in range(5)])

    card = DashboardRuntime.scorecard(_runtime(memory))
    assert card["resolved"] == 5, \
        "every outcome must be scored regardless of how much noise is newer"


def test_it_does_not_read_a_recent_window_at_all():
    """A window is the bug. Any window large enough today is a window that is
    too small once the capped rows accumulate again."""
    memory = _Memory(deliberations=[_delib("t0")], outcomes=[_outcome("t0")])
    DashboardRuntime.scorecard(_runtime(memory))
    assert memory.recent_calls == 0, "joined from the outcomes side"


def test_the_count_matches_the_outcomes_that_have_a_deliberation():
    memory = _Memory(
        deliberations=[_delib("t0"), _delib("t1")],
        # t2's deliberation was pruned; it cannot be scored and must not count.
        outcomes=[_outcome("t0"), _outcome("t1"), _outcome("t2")])
    assert DashboardRuntime.scorecard(_runtime(memory))["resolved"] == 2


def test_no_outcomes_is_zero_not_an_error():
    memory = _Memory(deliberations=[_capped("c0")], outcomes=[])
    card = DashboardRuntime.scorecard(_runtime(memory))
    assert card["resolved"] == 0


def test_an_unreadable_memory_degrades_rather_than_raising():
    """This feeds a dashboard panel and the paper report. It must not take
    either down."""
    class _Broken:
        def resolved_outcomes(self, limit=500): raise RuntimeError("db gone")
        def get_deliberation(self, tid): return None
        def recent_deliberations(self, limit=20, status=None): return []

    card = DashboardRuntime.scorecard(_runtime(_Broken()))
    assert card["resolved"] == 0
    assert card["committee"] is None


def test_the_seat_verdicts_survive_the_join():
    """The point of scoring: per-seat numbers, not just a count."""
    memory = _Memory(
        deliberations=[_capped(f"c{i}") for i in range(600)]
                      + [_delib("t0"), _delib("t1", signal="bullish")],
        outcomes=[_outcome("t0", 0.05), _outcome("t1", -0.05)])

    card = DashboardRuntime.scorecard(_runtime(memory))
    quant = next(s for s in card["seats"] if s["seat_id"] == "quant")
    assert quant["samples"] == 2
    assert quant["hit_rate"] == pytest.approx(0.5)
    assert quant["brier"] > 0, "a Brier score needs the confidences too"
