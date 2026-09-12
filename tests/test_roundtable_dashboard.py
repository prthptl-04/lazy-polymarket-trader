"""Round-table monitoring UI.

The page exists to show DISAGREEMENT. Tests focus on the things that would
quietly mislead if they broke: unanimity going unflagged, abstentions being
counted as votes, a fallback tally presented as a synthesis.
"""

import pytest
from fastapi.testclient import TestClient

from dashboard.runtime import build_runtime
from dashboard.server import create_app
from memory.store import MemoryStore
from trading.autonomous_loop import WatchedMarket


class _StubClient:
    def post_order(self, order): return {"orderID": "x"}
    def cancel_order(self, order_id): return {"ok": True}


def _payload(signals, consensus=None, failed=()):
    opinions = []
    for i, sig in enumerate(signals):
        name = f"Seat {i}"
        if name in failed:
            opinions.append({
                "seat_id": f"s{i}", "seat_name": name, "signal": "neutral",
                "confidence": 0.0, "reasoning": "", "key_points": [],
                "concerns": [], "failed": True, "error": "api down",
            })
            continue
        opinions.append({
            "seat_id": f"s{i}", "seat_name": name, "signal": sig,
            "confidence": 70.0, "reasoning": f"reason {i}",
            "key_points": ["kp"], "concerns": ["c"], "failed": False, "error": None,
        })
    tally = {"bullish": 0, "bearish": 0, "neutral": 0}
    for o in opinions:
        if not o["failed"]:
            tally[o["signal"]] += 1
    return {"opinions": opinions, "tally": tally, "consensus": consensus}


@pytest.fixture
def client(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "d.db"))
    rt = build_runtime(
        polymarket_client=_StubClient(),
        watched=[WatchedMarket(market_id="m1", token_id="tok-a")],
        memory=store,
    )
    return TestClient(create_app(rt)), store


# ---------------- page + routes ----------------

def test_roundtable_page_renders(client):
    c, _ = client
    r = c.get("/roundtable")
    assert r.status_code == 200
    assert "Round Table" in r.text
    assert "/api/deliberations" in r.text


def test_dashboard_links_to_the_round_table(client):
    c, _ = client
    assert "/roundtable" in c.get("/").text


def test_empty_index(client):
    c, _ = client
    assert c.get("/api/deliberations").json() == []


def test_unknown_thesis_is_404(client):
    c, _ = client
    r = c.get("/api/deliberations/nope")
    assert r.status_code == 404
    assert "error" in r.json()


# ---------------- index ----------------

def test_index_lists_deliberations(client):
    c, store = client
    store.save_deliberation(
        "t1", "AAPL", "equity", "complete",
        _payload(["bullish", "bullish", "bearish"],
                 consensus={"signal": "bullish", "confidence": 72.0}),
        signal="bullish", confidence=72.0,
    )
    rows = c.get("/api/deliberations").json()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["signal"] == "bullish"
    assert rows[0]["seats"] == 3
    assert rows[0]["tally"]["bullish"] == 2


def test_in_progress_deliberations_appear(client):
    c, store = client
    store.save_deliberation("t1", "AAPL", "equity", "in_progress", _payload(["bullish"]))
    assert c.get("/api/deliberations").json()[0]["status"] == "in_progress"


# ---------------- the warnings that matter ----------------

def test_unanimity_is_flagged(client):
    c, store = client
    store.save_deliberation(
        "t1", "AAPL", "equity", "complete",
        _payload(["bullish"] * 5, consensus={"signal": "bullish", "confidence": 90.0}),
        signal="bullish", confidence=90.0,
    )
    d = c.get("/api/deliberations/t1").json()
    assert d["unanimous"] is True


def test_split_vote_is_not_flagged_as_unanimous(client):
    c, store = client
    store.save_deliberation(
        "t1", "AAPL", "equity", "complete",
        _payload(["bullish", "bullish", "bearish"]),
    )
    assert c.get("/api/deliberations/t1").json()["unanimous"] is False


def test_single_seat_is_not_unanimity(client):
    """One voice agreeing with itself is not a consensus."""
    c, store = client
    store.save_deliberation("t1", "AAPL", "equity", "complete", _payload(["bullish"]))
    assert c.get("/api/deliberations/t1").json()["unanimous"] is False


def test_abstentions_are_named_and_excluded_from_the_tally(client):
    c, store = client
    store.save_deliberation(
        "t1", "AAPL", "equity", "complete",
        _payload(["bullish", "bullish", "bearish"], failed=("Seat 1",)),
    )
    d = c.get("/api/deliberations/t1").json()
    assert d["abstentions"] == ["Seat 1"]
    assert sum(d["tally"].values()) == 2        # the abstainer did not vote


def test_page_warns_about_unanimity_and_abstentions():
    from dashboard.roundtable_view import ROUNDTABLE_HTML
    assert "No dissent" in ROUNDTABLE_HTML
    assert "Abstained" in ROUNDTABLE_HTML
    assert "Fallback tally" in ROUNDTABLE_HTML


# ---------------- detail ----------------

def test_detail_carries_seats_and_transcript(client):
    c, store = client
    store.save_deliberation(
        "t1", "AAPL", "equity", "complete",
        _payload(["bullish", "bearish"], consensus={
            "signal": "bullish", "confidence": 65.0, "summary": "resolved",
            "dissent": "risk objected", "transcript": "[Chair]: settled",
            "synthesized_by_llm": True,
        }),
        signal="bullish", confidence=65.0,
    )
    d = c.get("/api/deliberations/t1").json()
    assert len(d["opinions"]) == 2
    assert d["consensus"]["transcript"] == "[Chair]: settled"
    assert d["consensus"]["dissent"] == "risk objected"


def test_detail_survives_a_missing_consensus(client):
    c, store = client
    store.save_deliberation("t1", "AAPL", "equity", "in_progress", _payload(["bullish"]))
    d = c.get("/api/deliberations/t1").json()
    assert d["consensus"] == {}
    assert d["status"] == "in_progress"


def test_html_escapes_untrusted_model_text():
    """Seat reasoning is model output rendered into a page."""
    from dashboard.roundtable_view import ROUNDTABLE_HTML
    assert "const esc" in ROUNDTABLE_HTML
    # Every interpolation of model-authored text goes through esc().
    for field in ("o.reasoning", "c.summary", "c.transcript", "o.seat_name"):
        assert f"esc({field})" in ROUNDTABLE_HTML
