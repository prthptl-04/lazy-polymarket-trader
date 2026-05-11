import os
from pathlib import Path

from memory.store import MemoryStore
from trading.execution import Executor
from verification.criteria import VerifiedOutcomeCriteria
from verification.outcome_grader import OutcomeGrader, ProposedTrade


def _good_trade() -> ProposedTrade:
    return ProposedTrade(
        market_id="m1",
        side="YES",
        size_usd=10.0,
        price=0.5,
        orderbook_depth_usd=2000.0,
        expected_edge_bps=40,
        estimated_slippage_bps=10,
    )


def test_paper_trade_passes_and_logs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "true")
    store = MemoryStore(db_path=str(tmp_path / "test.db"))
    executor = Executor(grader=OutcomeGrader(), memory=store)

    result = executor.execute(agent_id="architect", trade=_good_trade())

    assert result.accepted is True
    assert result.paper is True
    assert result.grade.passed is True
    assert result.trade_log_id is not None


def test_grader_rejection_blocks_execution(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "true")
    store = MemoryStore(db_path=str(tmp_path / "test.db"))
    grader = OutcomeGrader(VerifiedOutcomeCriteria(max_position_usd=1.0))
    executor = Executor(grader=grader, memory=store)

    result = executor.execute(agent_id="architect", trade=_good_trade())

    assert result.accepted is False
    assert result.grade.passed is False
    assert result.grade.rejected_rule == "max_position_usd"


def test_live_mode_falls_back_when_keys_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS", raising=False)

    store = MemoryStore(db_path=str(tmp_path / "test.db"))
    executor = Executor(grader=OutcomeGrader(), memory=store)

    result = executor.execute(agent_id="architect", trade=_good_trade())

    # Live disallowed without wallet config — runs as paper, not live.
    assert result.paper is True
    assert result.accepted is True
