"""Put the committee in front of past equity sessions, tonight.

The question: can these seats trade equities, and can we find out before
spending a session discovering it? Live calibration needs 30 resolved outcomes
at roughly one deliberation per cycle — days of waiting, and every learning
mechanism stays switched off until then. The same seats can be put in front of
sessions that have already happened, and the market has already answered.

Three ways a replay lies, and what is done about each.

**Look-ahead in the bars.** The candidate is built from a strict slice ending at
the decision bar, and the forward return comes from bars after it that the
candidate never held. Unreachable by construction, not by convention.

**Look-ahead in the evidence.** News, fundamentals, catalysts and post-mortem
lessons cannot be reliably reconstructed as-of a past date — the news endpoint
returns what is recent NOW, and SEC facts arrive revised. So they are not
supplied at all, and the evidence block says NOT AVAILABLE rather than going
quiet. That UNDERSTATES what the committee knows, which is the safe direction:
a replay that flattered the seats with tomorrow's headlines would be worse than
no replay, because it would produce confident weights on a fiction.

What that leaves is price structure, volatility, liquidity and the exit plan —
which is exactly the evidence the Quant and the Risk Manager own, and they are
the seats that carry a crypto table today.

**Contaminating the live record.** Replay outcomes are marked with
`REPLAY_NOTE`, excluded from the confidence-shrink fit, and cannot count toward
the 50 paper trades rule #13 requires. They calibrate seats. They do not earn a
live flip.

    python -m backtest.committee --limit 30
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from roundtable.types import Candidate

logger = logging.getLogger(__name__)

# Marks an outcome as produced by a historical replay.
REPLAY_NOTE = "replay: scored against past bars, no position was taken"

# Bars ahead of the decision the call is judged over. One session, matching the
# fund's swing horizon — an hour is noise and a month is a different thesis.
DEFAULT_HORIZON = 1
# Bars of history each candidate is built from, matching `lookback_bars`.
DEFAULT_LOOKBACK = 60


def score_replay(signal: Optional[str], forward_return: float) -> Optional[float]:
    """The move, signed so that "right" is positive.

    A correct bearish call shows a negative price change; scoring on the raw
    sign would mark every right short as a loss. `None` for neutral — it made no
    directional claim, so there is nothing to be right about.
    """
    if signal == "bullish":
        return forward_return
    if signal == "bearish":
        return -forward_return
    return None


@dataclass
class ReplayCandidate:
    """One past decision point, with the future kept out of reach."""

    symbol: str
    bars: list                      # strictly up to and including the decision
    price: float
    forward_return: float           # from bars the candidate never held

    @classmethod
    def build(cls, symbol: str, bars: Sequence[Any], *, at: int,
              horizon: int = DEFAULT_HORIZON,
              lookback: int = DEFAULT_LOOKBACK) -> Optional["ReplayCandidate"]:
        """Slice history at `at`. None when there is no future to score against.

        Refusing rather than scoring a missing future as flat: a fabricated
        sample is worse than a missing one, because it dilutes the record the
        seats are judged on.
        """
        if at >= len(bars) - horizon or at < 1:
            return None
        window = list(bars[max(0, at - lookback + 1): at + 1])
        decision_close = float(window[-1].close)
        later_close = float(bars[at + horizon].close)
        if decision_close <= 0:
            return None
        return cls(symbol=symbol, bars=window, price=decision_close,
                   forward_return=(later_close - decision_close) / decision_close)

    def as_candidate(self) -> Candidate:
        """The evidence block, built from the same helpers the live path uses.

        Everything that cannot be reconstructed as-of the decision date is left
        empty, so `Candidate._missing_fields` names it as NOT AVAILABLE. A seat
        reading an absent news block as a quiet tape would be drawing a
        conclusion from our inability to fetch history.
        """
        from trading.candidate_builder import build_candidate

        closes = [float(b.close) for b in self.bars]
        returns = [(b - a) / a for a, b in zip(closes, closes[1:]) if a]
        built = build_candidate(
            symbol=self.symbol, bars=self.bars, price=self.price,
            asset_class="equity", session="regular",
            # A replay cannot know the spread that was quoted at the time, and
            # inventing one would make the grader's cost gate meaningless.
            spread_bps=None,
            returns=returns,
            dollar_volumes=[getattr(b, "volume", 0.0) * c
                            for b, c in zip(self.bars, closes)],
        )
        return built.candidate


async def replay_one(table: Any, candidate: ReplayCandidate,
                     memory: Any) -> Optional[str]:
    """Deliberate one past decision and record the outcome. Returns the signal."""
    thesis = await table.deliberate(candidate.as_candidate())
    signal = thesis.signal
    realized = score_replay(signal, candidate.forward_return)
    try:
        memory.record_thesis_outcome(
            thesis.thesis_id, candidate.symbol,
            realized if realized is not None else 0.0,
            signal=signal, confidence=thesis.confidence,
            correct=None if realized is None else realized > 0,
            notes=REPLAY_NOTE)
    except Exception:
        logger.exception("could not record a replay outcome")
    return signal


def _demo() -> None:
    class _B:
        def __init__(self, c): self.close = self.open = c; self.high = c + 1; self.low = c - 1
        volume = 1_000_000.0

    bars = [_B(100.0 + i) for i in range(40)]
    c = ReplayCandidate.build("AAPL", bars, at=20)
    assert c is not None and len(c.bars) == 21, c
    assert c.bars[-1] is bars[20], "the decision bar must be the last one held"
    assert abs(c.forward_return - (1 / 120)) < 1e-9, c.forward_return
    assert ReplayCandidate.build("AAPL", bars, at=39) is None, "no future to score"
    assert score_replay("bearish", -0.04) == 0.04
    assert score_replay("neutral", 0.09) is None
    print("committee replay self-check passed")


async def _main(args: Any) -> int:
    """Walk each symbol's history, deliberate at spaced decision points, score.

    Decision points are SPACED rather than consecutive: adjacent bars share
    almost all their history, so debating both buys one sample's worth of
    information at two samples' cost — and records it as two, which would
    overstate the confidence in every weight derived from it.
    """
    import os

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    from cache.cost_ledger import CostLedger
    from cache.gemini_backend import GeminiBackend
    from cache.llm_router import LlmRouter
    from dashboard.fund_wiring import _default_client
    from memory.store import MemoryStore
    from roundtable.engine import RoundTable
    from trading.massive_provider import MassiveProvider

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    provider = MassiveProvider()
    memory = MemoryStore()

    # Spread the budget across symbols so one name cannot dominate the record.
    per_symbol = max(1, args.limit // max(1, len(symbols)))
    plan: list[ReplayCandidate] = []
    for symbol in symbols:
        history = await provider.get_history(symbol, lookback=240)
        bars = getattr(history, "bars", None) or []
        if len(bars) < DEFAULT_LOOKBACK + args.horizon + 5:
            print(f"  {symbol}: only {len(bars)} bars, skipping")
            continue
        # Spaced by the lookback/4 so consecutive decisions do not share
        # almost all their evidence.
        step = max(5, DEFAULT_LOOKBACK // 4)
        last = len(bars) - args.horizon - 1
        points = list(range(DEFAULT_LOOKBACK, last, step))[-per_symbol:]
        for at in points:
            c = ReplayCandidate.build(symbol, bars, at=at, horizon=args.horizon)
            if c is not None:
                plan.append(c)

    plan = plan[: args.limit]
    print(f"\n{len(plan)} decision points across {len(symbols)} symbols")
    print(f"cost: ~{len(plan) * 8} model calls\n")

    if args.dry_run:
        for c in plan[:12]:
            print(f"  {c.symbol:6} price {c.price:9.2f}  "
                  f"forward {c.forward_return * 100:+.2f}%")
        print("\n(dry run — nothing was spent)")
        return 0

    # The same stack the live path builds: cached prompts, Gemini failover,
    # and the cost ledger — so a replay's spend lands in the same place the
    # committee's does and the burn figure stays one number.
    client = _default_client()
    table = RoundTable(
        client=client, memory=memory,
        router=LlmRouter(client=client, gemini=GeminiBackend(),
                         ledger=CostLedger(memory=memory)))
    right = wrong = neutral = 0
    for i, c in enumerate(plan, 1):
        try:
            signal = await replay_one(table, c, memory)
        except Exception as e:
            print(f"  [{i}/{len(plan)}] {c.symbol}: FAILED {type(e).__name__}")
            continue
        scored = score_replay(signal, c.forward_return)
        mark = "—" if scored is None else ("HIT " if scored > 0 else "miss")
        if scored is None:
            neutral += 1
        elif scored > 0:
            right += 1
        else:
            wrong += 1
        print(f"  [{i}/{len(plan)}] {c.symbol:6} {str(signal):8} "
              f"forward {c.forward_return * 100:+6.2f}%  {mark}")

    decided = right + wrong
    print(f"\ndirectional {decided}/{len(plan)}  (neutral {neutral})")
    if decided:
        print(f"hit rate    {right / decided:.1%}  "
              f"(break-even at 1.5R is 40%)")
    print("\nSeat scores now include these. Run:")
    print("  python -m monitoring.paper_report")
    return 0


if __name__ == "__main__":
    import argparse
    import asyncio
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--limit", type=int, default=30,
                        help="deliberations to run (8 model calls each)")
    parser.add_argument("--symbols", default="AAPL,MSFT,NVDA,AMD,META,GOOGL,AMZN,TSLA")
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--dry-run", action="store_true",
                        help="build the candidates and print them, spend nothing")
    args = parser.parse_args()

    if args.demo:
        _demo()
        raise SystemExit(0)

    raise SystemExit(asyncio.run(_main(args)))
