from pathlib import Path

import pytest

from memory.store import MemoryStore
from trading.execution import Executor, _env_live_intent, _live_preconditions
from verification.criteria import (
    LIVE_APPROVAL_LESSON,
    MIN_PAPER_TRADES_FOR_LIVE,
)
from verification.outcome_grader import OutcomeGrader, ProposedTrade


def _good_trade() -> ProposedTrade:
    return ProposedTrade(
        market_id="m1", side="YES",
        size_usd=5.0, price=0.5,
        orderbook_depth_usd=2000.0,
        expected_edge_bps=40,
        estimated_slippage_bps=10,
    )


def _seed_paper_trades(memory: MemoryStore, count: int) -> None:
    for i in range(count):
        memory.log_trade(
            agent_id="architect", market_id=f"m{i}", side="YES",
            size=1.0, price=0.5, paper=True, grade_pass=True,
            grade_reason="verified outcome",
        )


# -------- env intent --------

def test_env_intent_false_by_default(monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS", raising=False)
    assert _env_live_intent() is False


def test_env_intent_true_when_all_set(monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0xdef")
    assert _env_live_intent() is True


# -------- preconditions: paper count + approval --------

def test_preconditions_block_when_no_paper_trades(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0xdef")
    mem = MemoryStore(db_path=str(tmp_path / "m.db"))
    allowed, reason = _live_preconditions(mem)
    assert not allowed
    assert "insufficient paper validation" in reason


def test_preconditions_block_when_approval_lesson_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0xdef")
    mem = MemoryStore(db_path=str(tmp_path / "m.db"))
    _seed_paper_trades(mem, MIN_PAPER_TRADES_FOR_LIVE)
    allowed, reason = _live_preconditions(mem)
    assert not allowed
    assert "explicit user approval" in reason


def test_preconditions_pass_when_all_three_satisfied(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0xdef")
    mem = MemoryStore(db_path=str(tmp_path / "m.db"))
    _seed_paper_trades(mem, MIN_PAPER_TRADES_FOR_LIVE)
    mem.record_lesson("*", LIVE_APPROVAL_LESSON)
    allowed, reason = _live_preconditions(mem)
    assert allowed, reason
    assert reason is None


# -------- executor end-to-end --------

def test_executor_runs_paper_when_preconditions_unmet(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    mem = MemoryStore(db_path=str(tmp_path / "m.db"))
    executor = Executor(grader=OutcomeGrader(), memory=mem)
    result = executor.execute(agent_id="architect", trade=_good_trade())
    assert result.paper is True
    assert result.downgrade_reason is not None


def test_executor_goes_live_when_all_gates_pass(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", "0xdef")

    mem = MemoryStore(db_path=str(tmp_path / "m.db"))
    _seed_paper_trades(mem, MIN_PAPER_TRADES_FOR_LIVE)
    mem.record_lesson("*", LIVE_APPROVAL_LESSON)

    class _FakePoly:
        posted: list = []
        def post_order(self, order): self.posted.append(order); return {"ok": True}

    poly = _FakePoly()
    executor = Executor(grader=OutcomeGrader(), memory=mem, polymarket=poly)
    result = executor.execute(agent_id="architect", trade=_good_trade())
    assert result.paper is False
    assert result.accepted is True
    assert poly.posted, "live path did not call polymarket.post_order"
