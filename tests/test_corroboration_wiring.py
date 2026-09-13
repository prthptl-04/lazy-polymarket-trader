"""The Corroborator seat must reason from something.

The module was written, tested and never wired: `build_candidate` had no
`corroboration_notes` parameter, so `Candidate.corroboration_notes` was always
empty and the seat voted on a blank evidence section every deliberation — while
still counting toward the quorum and the tally. Apparent independence that was
not there.

- Blind: a FAILED cross-check must not read as a clean one. Silence would let
  the seat treat single-sourced figures as verified.
"""

import pytest

from roundtable.types import Candidate
from trading.candidate_builder import build_candidate
from trading.fund import FundLoop
from finance.exits import Bar


def _bars(n=30, price=100.0):
    return [Bar(high=price + 1, low=price - 1, close=price) for _ in range(n)]


def test_the_evidence_block_carries_corroboration():
    built = build_candidate(symbol="AAPL", bars=_bars(), price=100.0,
                            corroboration_notes=("price agrees within 2 bps",))
    assert built.candidate.corroboration_notes == ("price agrees within 2 bps",)
    assert "CORROBORATION" in built.candidate.evidence_block()


def test_a_candidate_without_corroboration_says_nothing_rather_than_pretending():
    built = build_candidate(symbol="AAPL", bars=_bars(), price=100.0)
    assert "CORROBORATION" not in built.candidate.evidence_block()


@pytest.mark.asyncio
async def test_the_fund_runs_the_corroborator():
    class Report:
        def evidence_lines(self): return ("price: 100.00 vs 100.02 — agree",)

    class Stub:
        async def run(self, symbol, *, scrape=False): return Report()

    fund = FundLoop(router=None, pipeline=None, round_table=None, data=None,
                    corroborator=Stub())
    assert await fund._corroboration_notes("AAPL") == ("price: 100.00 vs 100.02 — agree",)


@pytest.mark.asyncio
async def test_a_failed_cross_check_warns_rather_than_going_quiet():
    class Broken:
        async def run(self, symbol, *, scrape=False): raise RuntimeError("provider down")

    fund = FundLoop(router=None, pipeline=None, round_table=None, data=None,
                    corroborator=Broken())
    notes = await fund._corroboration_notes("AAPL")
    assert notes and "single-sourced" in notes[0]


@pytest.mark.asyncio
async def test_no_corroborator_configured_is_empty_not_reassuring():
    fund = FundLoop(router=None, pipeline=None, round_table=None, data=None)
    assert await fund._corroboration_notes("AAPL") == ()
