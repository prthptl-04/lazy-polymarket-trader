from pathlib import Path

from memory.store import MemoryStore


def test_put_and_get_roundtrip(tmp_path: Path):
    store = MemoryStore(db_path=str(tmp_path / "test.db"))
    store.put("architect", "last_phase", {"phase": 0, "status": "ok"})
    got = store.get("architect", "last_phase")
    assert got == {"phase": 0, "status": "ok"}


def test_get_returns_default_for_missing(tmp_path: Path):
    store = MemoryStore(db_path=str(tmp_path / "test.db"))
    assert store.get("product", "missing", default="fallback") == "fallback"


def test_trade_log_records_grade(tmp_path: Path):
    store = MemoryStore(db_path=str(tmp_path / "test.db"))
    log_id = store.log_trade(
        agent_id="architect",
        market_id="m1",
        side="YES",
        size=10.0,
        price=0.5,
        paper=True,
        grade_pass=True,
        grade_reason="verified outcome",
    )
    assert log_id is not None
    recent = store.recent_trades(limit=5)
    assert len(recent) == 1
    assert recent[0]["grade_pass"] == 1
    assert recent[0]["paper"] == 1
