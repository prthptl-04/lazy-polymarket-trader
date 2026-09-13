"""The post-mortem may not teach what it did not observe.

Finding #5 fired on EVERY stopped-out bullish position with "the stop may have
been sized to noise" — knowing neither the ATR multiple nor the post-exit path.
Those lines go into every later deliberation's evidence block, so a run of
ordinary stop-outs taught the committee to widen stops on no evidence, turning a
planned 2R loss into a larger one.

- Blind: a stop at a normal multiple that was simply hit teaches NOTHING.
- Edge: no plan, or a zero ATR, must produce silence rather than a guess.
"""

import pytest

from roundtable.postmortem import Postmortem


def _thesis(confidence=70.0):
    return {"payload": {"consensus": {"signal": "bullish", "confidence": confidence},
                        "opinions": [], "tally": {"bullish": 2, "bearish": 1}}}


def _codes(plan, realized=-0.04, confidence=70.0):
    return {f.code for f in Postmortem().analyse(
        symbol="AAPL", realized_return=realized, thesis=_thesis(confidence),
        exit_reason="stop", plan=plan)}


def test_a_normal_stop_that_was_hit_teaches_nothing():
    """2x ATR reached is a stop working, not a stop mis-set."""
    assert "stop_inside_noise" not in _codes({"entry": 100.0, "stop": 96.0, "atr": 2.0})
    assert "stop_overshot" not in _codes({"entry": 100.0, "stop": 96.0, "atr": 2.0})


def test_a_stop_inside_one_atr_is_named():
    codes = _codes({"entry": 100.0, "stop": 99.0, "atr": 2.0}, realized=-0.01)
    assert "stop_inside_noise" in codes


def test_a_loss_that_overshot_the_plan_is_named():
    """Lost 8% against a planned 4% stop — a gap or a bad fill, not a wrong thesis."""
    codes = _codes({"entry": 100.0, "stop": 96.0, "atr": 2.0}, realized=-0.08)
    assert "stop_overshot" in codes


def test_no_plan_means_no_finding():
    assert _codes(None) == _codes({}) 
    assert "stop_inside_noise" not in _codes(None)
    assert "stop_overshot" not in _codes(None)


def test_a_zero_atr_produces_silence_not_a_division():
    assert "stop_inside_noise" not in _codes({"entry": 100.0, "stop": 99.0, "atr": 0.0})


def test_other_findings_still_fire_on_a_loss():
    """Only finding #5 was ungrounded; the rest are arithmetic on real fields."""
    codes = _codes({"entry": 100.0, "stop": 96.0, "atr": 2.0},
                   realized=-0.05, confidence=95.0)
    assert "overconfident_loss" in codes
