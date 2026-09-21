"""The self-evolution loop, assembled as one readable object.

Framing borrowed from aiwaves-cn/agents (Agents 2.0, Apache-2.0), whose
analogy is that an agent pipeline is a computational graph: a node is a layer,
its prompts and tools are that layer's weights, and textual reflections
back-propagate as "language gradients". None of its code is used.

The analogy fits what is already here, which is why it is worth drawing:

    forward   evidence -> six seats -> chair -> grader -> router -> outcome
    weights   per-seat vote_weight, and the confidence shrink that sizes
    loss      realised return, per-seat Brier, overconfidence
    backward  postmortem lessons, injected into the NEXT deliberation

The last one is not a metaphor: `recent_lesson_lines` puts a textual reflection
from a loss into every subsequent evidence block. That is a language gradient
reaching a prompt.

This endpoint exists so the page makes ONE request instead of six, and so the
loop is described in one place rather than reassembled in the browser. Every
number in it is real — a decorative animation on a trading dashboard is worse
than no animation, because it implies learning that is not happening.
"""

import pytest

from dashboard.runtime import DashboardRuntime
from memory.store import MemoryStore


def _rt(tmp_path):
    return DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "e.db")))


def test_the_loop_has_all_four_stages(tmp_path):
    e = _rt(tmp_path).evolution()
    assert set(e) >= {"forward", "weights", "loss", "backward"}


def test_the_forward_pass_names_the_real_pipeline(tmp_path):
    """The stages must be the ones the fund actually runs, not an idealised
    diagram — a picture of a system that does not exist teaches the wrong thing."""
    stages = [s["id"] for s in _rt(tmp_path).evolution()["forward"]]
    assert stages == ["evidence", "seats", "chair", "grader", "router", "outcome"]


def test_every_seat_reports_the_weight_actually_applied(tmp_path):
    """Not a recommendation. This is the number `Thesis.weighted_tally`
    multiplies by, read from the same place, so the picture cannot drift from
    the behaviour."""
    weights = _rt(tmp_path).evolution()["weights"]["seats"]
    assert len(weights) >= 6
    assert all("vote_weight" in w and "seat_id" in w for w in weights)
    assert all(w["vote_weight"] == 1.0 for w in weights), "unscored means unweighted"


def test_the_sizing_weight_is_the_shrink_in_force(tmp_path):
    e = _rt(tmp_path).evolution()
    assert "confidence_shrink" in e["weights"]


def test_loss_is_empty_and_says_so_before_anything_closes(tmp_path):
    """Refusing to report a score on zero trades is the whole point of the
    sample gates elsewhere; this must not quietly invent one."""
    loss = _rt(tmp_path).evolution()["loss"]
    assert loss["closed_trades"] == 0
    assert loss["reason"]


def test_the_backward_pass_shows_exactly_what_reaches_a_prompt(tmp_path):
    """The falsifiable claim: this panel is the SAME call the deliberation
    makes, not a second rendering of the lessons table.

    It matters because `recent_lesson_lines` is gated — under
    MIN_SAMPLES_FOR_FIT resolved trades it injects a "nothing learned yet"
    notice and withholds the recorded lessons, on the reasoning that a noisy
    lesson compounds across every later debate. A panel that drew from the
    lessons table instead would show a gradient flowing that is not flowing.
    """
    from roundtable.postmortem import recent_lesson_lines
    store = MemoryStore(db_path=str(tmp_path / "e.db"))
    store.record_lesson("*", "stops were too tight on gap-prone names")
    rt = DashboardRuntime(memory=store)

    back = rt.evolution()["backward"]
    assert [l["text"] for l in back["lessons"]] == list(recent_lesson_lines(store))
    assert back["injecting"] is False, "no resolved trades yet — the gate holds"
    assert back["recorded"] == 1, "the lesson is on file even while withheld"


def test_the_external_inputs_are_named(tmp_path):
    """'Utilising the internet' has to mean something specific, or it is
    decoration. These are the providers actually consulted."""
    inputs = _rt(tmp_path).evolution()["forward"][0]["inputs"]
    ids = {i["id"] for i in inputs}
    assert {"market", "news", "corroboration", "lessons"} <= ids


def test_a_broken_store_degrades_rather_than_five_hundreds(tmp_path):
    class _Broken:
        def __getattr__(self, n):
            def boom(*a, **k): raise RuntimeError("db gone")
            return boom
    e = DashboardRuntime(memory=_Broken()).evolution()
    assert e["loss"]["closed_trades"] == 0
    assert e["backward"]["lessons"] == []
