"""The paper-trading report, and the triggers it pre-registers.

The point of writing the triggers down BEFORE the results arrive: a fund that
decides after the fact what counts as evidence will always find evidence for
what it already wanted. Each trigger names what to watch, the threshold, and
the change it implies — and is then evaluated against live state rather than
recalled.

The report must be honest about an empty fund. On day zero almost every
trigger is un-evaluable, and saying "not yet" is the correct output. A report
that rendered green because nothing had failed would be worse than none.
"""

import pytest

from memory.store import MemoryStore
from monitoring.paper_report import TRIGGERS, build_report, render_markdown


@pytest.fixture
def rt(tmp_path):
    from dashboard.runtime import DashboardRuntime
    return DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "r.db")))


def test_every_trigger_names_a_watch_a_threshold_and_an_action(rt):
    """A trigger without an action is an observation. The action is the whole
    point — it is what makes the threshold a commitment rather than a metric."""
    for t in TRIGGERS:
        assert t.watch and t.threshold and t.action, t.id
        assert len(t.action) > 20, f"{t.id}: action is too vague to act on"


def test_trigger_ids_are_unique(rt):
    assert len({t.id for t in TRIGGERS}) == len(TRIGGERS)


def test_an_empty_fund_reports_not_yet_rather_than_pass(rt):
    """Day zero. Nothing has failed because nothing has happened, and a green
    report would say the opposite of the truth."""
    report = build_report(rt)
    assert report["resolved"] == 0
    evaluated = [t for t in report["triggers"] if t["state"] != "not_evaluable"]
    assert evaluated == [], f"nothing should be evaluable yet, got {evaluated}"


def test_the_two_thresholds_that_gate_everything_are_reported(rt):
    """50 graded paper trades unlocks the live gate; 30 resolved unlocks the
    learning. Every dormant mechanism is waiting on one of these."""
    report = build_report(rt)
    assert report["gates"]["live_trading"]["required"] == 50
    assert report["gates"]["learning"]["required"] == 30


def test_dormant_mechanisms_are_listed_with_what_they_wait_on(rt):
    report = build_report(rt)
    names = {d["name"] for d in report["dormant"]}
    assert {"seat vote weights", "confidence shrink", "post-mortem lessons"} <= names
    assert all(d["waiting_on"] for d in report["dormant"])


def _committee(rt, wins: int, n: int = 30):
    """A committee that spoke AND was resolved.

    Both halves are required: the hit rate is the committee's calls scored
    against outcomes, so outcomes alone cannot produce one. A fund with 30
    resolved theses and no stored deliberations is correctly un-scorable.
    """
    for i in range(n):
        rt.memory.save_deliberation(
            f"t{i}", "AAPL", "equity", "complete",
            {"opinions": [{"seat_id": "quant", "seat_name": "Quant",
                           "signal": "bullish", "confidence": 70.0, "failed": False}],
             "consensus": {"signal": "bullish", "confidence": 70.0},
             "tally": {"bullish": 1}},
            signal="bullish", confidence=70.0)
        rt.memory.record_thesis_outcome(
            f"t{i}", "AAPL", 0.01 if i < wins else -0.01,
            signal="bullish", confidence=70.0, correct=i < wins)


def test_a_losing_committee_fires_the_edge_trigger(rt):
    """Break-even at 1.5R is a 40% hit rate, not 50%. A trigger set at 50%
    would fire on a fund that is making money."""
    _committee(rt, wins=9)                       # 30%
    fired = {t["id"] for t in build_report(rt)["triggers"] if t["state"] == "fired"}
    assert "edge_below_breakeven" in fired


def test_a_winning_committee_does_not_fire_it(rt):
    _committee(rt, wins=18)                      # 60%
    states = {t["id"]: t["state"] for t in build_report(rt)["triggers"]}
    assert states["edge_below_breakeven"] == "ok"


def test_a_hit_rate_between_breakeven_and_a_coin_flip_is_not_a_failure(rt):
    """45% loses money at 1:1 and makes it at 1.5R. This is the case a
    carelessly-set trigger gets wrong, so it is pinned."""
    _committee(rt, wins=14)                      # 46.7%
    states = {t["id"]: t["state"] for t in build_report(rt)["triggers"]}
    assert states["edge_below_breakeven"] == "ok"


def test_outcomes_without_deliberations_cannot_be_scored(rt):
    """Un-evaluable, not passing. There is no committee to have a hit rate."""
    for i in range(30):
        rt.memory.record_thesis_outcome(f"t{i}", "AAPL", -0.01,
                                        signal="bullish", confidence=70.0)
    states = {t["id"]: t["state"] for t in build_report(rt)["triggers"]}
    assert states["edge_below_breakeven"] == "not_evaluable"


def test_the_markdown_renders_without_a_single_trade(rt):
    md = render_markdown(build_report(rt))
    assert "Paper-trading report" in md
    assert "not yet evaluable" in md.lower()
    assert "| 0 | 50 |" in md, "the live gate must show progress toward 50"
