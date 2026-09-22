"""Calibration describes a committee. Two models are two committees.

`fit_confidence_shrink` maps stated confidence onto realised hit rate. It is
therefore a statement about a particular set of seats running on a particular
model — and nothing recorded which model that was.

Two Gemini-backed deliberations were already sitting in the record from
overnight failovers, unmarked and indistinguishable from the Opus ones beside
them. That was tolerable while failover only fired on a 429. It stopped being
tolerable on 2026-09-22, when the Anthropic account hit its spend cap and the
router learned to fail over on that too: the next outage would have moved the
whole committee to gemini-flash and poured its confidences into the same set.

A confidence of 60 from Opus and a confidence of 60 from gemini-flash are not
the same claim. One curve fitted across both describes neither.
"""

import pytest

from roundtable.engine import RoundTable
from roundtable.types import Thesis


def _table(*providers):
    t = object.__new__(RoundTable)
    t._providers_used = set(providers)
    return t


# ---------- what the label says ----------

def test_one_backend_is_named_plainly():
    assert RoundTable._backend_used(_table("anthropic")) == "anthropic"
    assert RoundTable._backend_used(_table("gemini")) == "gemini"


def test_a_failover_partway_through_is_marked_mixed():
    """Half the seats on one model and half on another is a committee that
    never existed as a whole. Attributing it to whichever seat happened to
    answer last would be a lie with a plausible shape."""
    assert RoundTable._backend_used(
        _table("anthropic", "gemini")) == "mixed:anthropic+gemini"


def test_the_mixed_label_is_stable_whichever_failed_over_first():
    a = RoundTable._backend_used(_table("gemini", "anthropic"))
    b = RoundTable._backend_used(_table("anthropic", "gemini"))
    assert a == b, "a calibration filter has to be able to match on it"


def test_nothing_answered_is_none_not_a_backend():
    """Every seat failed and no model was ever reached. Recording that as
    "anthropic" would file a dead committee's confidence of 0.0 into the
    calibration set for a live one."""
    assert RoundTable._backend_used(_table()) is None


# ---------- it reaches the stored artifact ----------

def test_the_payload_carries_the_backend():
    thesis = Thesis(symbol="ARM", asset_class="equity")
    thesis.backend = "gemini"
    assert thesis.as_payload()["backend"] == "gemini"


def test_a_thesis_defaults_to_no_backend():
    assert Thesis(symbol="ARM").backend is None
    assert Thesis(symbol="ARM").as_payload()["backend"] is None


def test_the_set_is_reset_per_sitting():
    """Without the reset, one failover would mark every later deliberation in
    the process as mixed forever."""
    import inspect

    src = inspect.getsource(RoundTable.deliberate)
    assert "self._providers_used = set()" in src
    assert src.index("_providers_used = set()") < src.index("_persist(thesis")


def test_the_engine_records_every_provider_not_just_the_last():
    import inspect

    src = inspect.getsource(RoundTable._call)
    assert "_providers_used.add" in src


# ---------- the fit excludes a different committee ----------

class _Memory:
    def __init__(self, outcomes, deliberations):
        self._o, self._d = outcomes, deliberations
    def resolved_outcomes(self, limit=1000): return list(self._o)
    def recent_deliberations(self, limit=1000): return list(self._d)


def _delib(tid, backend):
    return {"thesis_id": tid, "payload": {"backend": backend}}


def _outcome(tid):
    return {"thesis_id": tid, "realized_return": 0.05, "confidence": 60.0}


def test_outcomes_from_another_backend_are_dropped():
    from dashboard.fund_wiring import _same_backend

    memory = _Memory(
        [_outcome("a"), _outcome("b"), _outcome("c")],
        [_delib("a", "anthropic"), _delib("b", "gemini"),
         _delib("c", "anthropic")])
    kept, dropped = _same_backend(memory, memory.resolved_outcomes(), "anthropic")
    assert [o["thesis_id"] for o in kept] == ["a", "c"]
    assert dropped == 1


def test_a_mixed_deliberation_is_evidence_about_neither_model():
    from dashboard.fund_wiring import _same_backend

    memory = _Memory([_outcome("a")], [_delib("a", "mixed:anthropic+gemini")])
    kept, dropped = _same_backend(memory, memory.resolved_outcomes(), "anthropic")
    assert kept == [] and dropped == 1


def test_an_unlabelled_row_is_kept():
    """Rows pre-date the field. Discarding real history over a missing label
    would throw away most of the calibration set to guard against a
    contamination that had not happened yet."""
    from dashboard.fund_wiring import _same_backend

    memory = _Memory([_outcome("a")], [_delib("a", None)])
    kept, dropped = _same_backend(memory, memory.resolved_outcomes(), "anthropic")
    assert len(kept) == 1 and dropped == 0


def test_an_outcome_with_no_deliberation_row_is_kept():
    from dashboard.fund_wiring import _same_backend

    memory = _Memory([_outcome("orphan")], [])
    kept, dropped = _same_backend(memory, memory.resolved_outcomes(), "anthropic")
    assert len(kept) == 1 and dropped == 0


def test_an_unreadable_memory_keeps_everything_rather_than_nothing():
    """This runs at build. Returning an empty set would silently switch
    calibration off; returning everything is the pre-existing behaviour."""
    from dashboard.fund_wiring import _same_backend

    class _Broken:
        def recent_deliberations(self, limit=1000): raise RuntimeError("db gone")

    kept, dropped = _same_backend(_Broken(), [_outcome("a")], "anthropic")
    assert len(kept) == 1 and dropped == 0


def test_no_backend_requested_keeps_every_outcome():
    """A caller with no router wants the old behaviour exactly."""
    from dashboard.fund_wiring import _fit_shrink

    memory = _Memory([_outcome("a"), _outcome("b")],
                     [_delib("a", "anthropic"), _delib("b", "gemini")])
    fit = _fit_shrink(memory, backend=None)
    assert fit is not None, "must still return a ShrinkFit, usable or not"


def test_the_fund_fits_for_the_backend_that_will_run_next():
    """Calibration is for the committee about to sit, not the union of every
    committee that ever sat."""
    import inspect

    from dashboard import fund_wiring
    src = inspect.getsource(fund_wiring.build_fund)
    assert "should_use_gemini()" in src
    assert "_fit_shrink(memory, backend=next_backend)" in src
