"""The committee is told which regime it is in, because the asymmetry inverts.

The operator's observation, and it is correct: 33 of 36 completed debates ended
neutral. Every learning mechanism in this fund is gated behind resolved trades —
seat weights at 30, the confidence shrink at 30, post-mortem lessons at 30 — and
a committee that never trades never resolves anything, so it never calibrates,
so it never improves. The system cannot bootstrap itself.

The cause is that the decision rule does not distinguish EXPLORATION from
EXPLOITATION. In paper mode with nothing resolved:

  - a position risks nothing, because no money is involved
  - a position produces a resolved outcome that scores every seat that spoke
  - a stand-aside produces NOTHING, and costs a cycle of calibration

That asymmetry is real, it is temporary, and the committee had no way to know
about it. It was applying live-money caution to a regime where being wrong is
free and information is the entire product.

This is not a loosened gate. Every gate is untouched — the grader, the router,
the net reward:risk floor, the kill switch. What changes is that the committee
is given a true fact about its own situation, the same way `budget_notes`
already tells it what it costs to run.

THE SAFETY PROPERTY, which matters more than the feature: this note must NEVER
appear when real money is at risk. A standing nudge toward trading, attached to
a live account, is the worst thing in this file. It is gated on paper mode AND
on the sample count, and it disappears the instant either changes.
"""

import pytest

from roundtable.types import Candidate
from trading.fund_config import cold_start_note


def _note(**kw):
    base = dict(paper=True, resolved=0, required=30)
    return cold_start_note(**{**base, **kw})


# ---------------------------------------------------------------- safety

def test_it_never_appears_when_real_money_is_at_risk():
    """The single most important assertion in this file. A standing nudge
    toward trading, attached to a live account, is indefensible."""
    assert cold_start_note(paper=False, resolved=0, required=30) == ""
    assert cold_start_note(paper=False, resolved=5, required=30) == ""


def test_it_disappears_once_the_fund_has_learned_enough():
    """Temporary by construction. At the sample bar the calibration machinery
    turns on and the exploration argument is spent."""
    assert _note(resolved=30) == ""
    assert _note(resolved=100) == ""


def test_it_is_present_only_in_the_gap_it_exists_for():
    assert _note(resolved=0) != ""
    assert _note(resolved=29) != ""


# ---------------------------------------------------------------- content

def test_it_states_the_regime_truthfully():
    note = _note(resolved=4)
    assert "paper" in note.lower()
    assert "4" in note and "30" in note


def test_it_names_the_asymmetry_rather_than_the_conclusion():
    """It supplies a fact. A line telling the chair to trade would be doing the
    job the chair exists to do, and would be a bias rather than evidence."""
    note = _note()
    assert "no information" in note.lower() or "teaches nothing" in note.lower()
    for banned in ("you should trade", "therefore trade", "take the trade",
                   "lower your bar", "be less cautious"):
        assert banned not in note.lower(), banned


def test_it_does_not_claim_the_gates_are_relaxed():
    """Every gate still applies. Implying otherwise would invite the committee
    to pass something the grader will refuse anyway."""
    note = _note()
    assert "still" in note.lower()


# ---------------------------------------------------------------- the block

def test_the_block_carries_it_when_given_one():
    block = Candidate(symbol="BTC-USD", asset_class="crypto", price=100.0,
                      regime_notes=(_note(),)).evidence_block()
    assert "paper" in block.lower()


def test_the_block_is_unchanged_without_one():
    """Live mode, or a calibrated fund: the evidence block must look exactly as
    it did before this existed."""
    block = Candidate(symbol="BTC-USD", asset_class="crypto", price=100.0).evidence_block()
    assert "paper" not in block.lower()


def test_the_gate_not_a_config_flag_decides_whether_it_appears(tmp_path):
    """A note that keyed off a config flag could appear against a live account
    through a disagreement between two settings. It reads the live gate — the
    thing that actually decides whether money can move."""
    from memory.store import MemoryStore
    from trading.fund import FundLoop

    class _Gate:
        def __init__(self, live): self._live = live
        def status(self): return {"live_possible": self._live}

    class _Router:
        def __init__(self, live): self.live_gate = _Gate(live)

    fund = FundLoop.__new__(FundLoop)
    fund.memory = MemoryStore(db_path=str(tmp_path / "c.db"))

    fund.router = _Router(live=False)          # paper only
    assert fund._regime_notes(), "paper with 0 resolved must carry the note"

    fund.router = _Router(live=True)           # money can move
    assert fund._regime_notes() == (), "never against a live account"


def test_an_unreadable_gate_yields_no_note(tmp_path):
    """Refuse by default, the same stance the gate itself takes: if we cannot
    prove it is paper, say nothing."""
    from memory.store import MemoryStore
    from trading.fund import FundLoop

    class _Broken:
        @property
        def live_gate(self): raise RuntimeError("gone")

    fund = FundLoop.__new__(FundLoop)
    fund.memory = MemoryStore(db_path=str(tmp_path / "c.db"))
    fund.router = _Broken()
    assert fund._regime_notes() == ()
