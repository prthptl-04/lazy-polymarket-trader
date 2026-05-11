from monitoring.live_feedback import LiveFeedback
from product.gap_analysis import detect_gaps


def test_detects_recurring_kind_above_threshold():
    fb = LiveFeedback()
    for _ in range(4):
        fb.record("grader_rejected", rule="max_position_usd")
    fb.record("clob_timeout")

    tickets = detect_gaps(fb.recent(), threshold=3)
    assert len(tickets) == 1
    assert tickets[0].kind == "grader_rejected"
    assert tickets[0].suggested_owner == "architect"


def test_below_threshold_does_not_emit():
    fb = LiveFeedback()
    fb.record("clob_5xx")
    tickets = detect_gaps(fb.recent(), threshold=3)
    assert tickets == []
