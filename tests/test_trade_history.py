"""Trade history with agent attribution.

"The committee was wrong" is not actionable. "The Quant was confidently wrong
while the Risk Manager objected" is — and that is the whole point of this view.

- Blind: blame is assigned ONLY on a loss, and only to seats whose own signal
  matched the consensus. A seat that dissented or abstained did not make the
  call and must not carry it.
- Edge: a thesis with no deliberation, an unresolved outcome, empty history.
"""

import pytest
from fastapi.testclient import TestClient

from dashboard.runtime import build_runtime
from dashboard.server import create_app
from memory.store import MemoryStore
from roundtable.postmortem import Postmortem


def _seed(tmp_path, *, correct: bool, realized: float):
    st = MemoryStore(db_path=str(tmp_path / "h.db"))
    st.save_deliberation("t1", "AAPL", "equity", "complete", {
        "opinions": [
            {"seat_id": "quant", "seat_name": "Quantitative Analyst",
             "signal": "bullish", "confidence": 88, "reasoning": "momentum", "failed": False},
            {"seat_id": "risk", "seat_name": "Risk Manager",
             "signal": "bearish", "confidence": 70, "reasoning": "stop in noise", "failed": False},
            {"seat_id": "corroborator", "seat_name": "Corroborator",
             "signal": "neutral", "confidence": 0, "reasoning": "", "failed": True},
        ],
        "consensus": {"signal": "bullish", "confidence": 84},
        "tally": {"bullish": 1, "bearish": 1, "neutral": 0},
    }, signal="bullish", confidence=84.0)
    st.record_thesis_outcome("t1", "AAPL", realized_return=realized,
                             signal="bullish", confidence=84.0, correct=correct)
    if not correct:
        Postmortem(memory=st).run(symbol="AAPL", realized_return=realized,
                                  thesis=st.get_deliberation("t1"))
    return st


def test_a_loss_blames_only_the_seats_that_backed_it(tmp_path):
    rows = build_runtime(memory=_seed(tmp_path, correct=False, realized=-0.062)).trade_history()
    r = rows[0]
    assert [b["name"] for b in r["blamed"]] == ["Quantitative Analyst"]
    assert [v["name"] for v in r["vindicated"]] == ["Risk Manager"]
    assert r["abstained"] == ["Corroborator"]


def test_a_win_blames_nobody(tmp_path):
    """Blame on a win would be noise; there is nothing to correct."""
    r = build_runtime(memory=_seed(tmp_path, correct=True, realized=0.07)).trade_history()[0]
    assert r["blamed"] == [] and r["vindicated"] == []
    assert r["won"] is True


def test_a_dissenter_is_never_blamed(tmp_path):
    r = build_runtime(memory=_seed(tmp_path, correct=False, realized=-0.05)).trade_history()[0]
    assert "Risk Manager" not in [b["name"] for b in r["blamed"]]


def test_an_abstainer_is_never_blamed(tmp_path):
    r = build_runtime(memory=_seed(tmp_path, correct=False, realized=-0.05)).trade_history()[0]
    assert "Corroborator" not in [b["name"] for b in r["blamed"]]


def test_self_improvement_actions_are_attached(tmp_path):
    r = build_runtime(memory=_seed(tmp_path, correct=False, realized=-0.062)).trade_history()[0]
    codes = {a["code"] for a in r["actions"]}
    assert "correct_dissent" in codes


def test_side_is_derived_from_the_consensus(tmp_path):
    r = build_runtime(memory=_seed(tmp_path, correct=False, realized=-0.05)).trade_history()[0]
    assert r["side"] == "buy" and r["realized_pct"] == pytest.approx(-5.0)


def test_an_outcome_without_a_deliberation_still_renders(tmp_path):
    st = MemoryStore(db_path=str(tmp_path / "h.db"))
    st.record_thesis_outcome("orphan", "TSLA", realized_return=-0.02, correct=False)
    r = build_runtime(memory=st).trade_history()[0]
    assert r["symbol"] == "TSLA" and r["blamed"] == [] and r["side"] == "—"


def test_empty_history(tmp_path):
    assert build_runtime(memory=MemoryStore(db_path=str(tmp_path / "h.db"))).trade_history() == []


def test_routes(tmp_path):
    st = _seed(tmp_path, correct=False, realized=-0.05)
    c = TestClient(create_app(build_runtime(memory=st)))
    assert c.get("/api/trade-history").json()[0]["symbol"] == "AAPL"
    latest = c.get("/api/roundtable/latest").json()
    assert len(latest["opinions"]) == 3


def test_latest_deliberation_404s_when_none(tmp_path):
    c = TestClient(create_app(build_runtime(memory=MemoryStore(db_path=str(tmp_path / "h.db")))))
    assert c.get("/api/roundtable/latest").status_code == 404
