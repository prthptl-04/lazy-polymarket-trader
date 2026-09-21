"""Re-decide resolved theses from their stored opinions. Costs nothing.

Rule #23's transferable lesson from the Kalshi research: when viability is the
open question, build the falsification test FIRST and make it cheap enough that
running it is never the expensive option. §11 of that document put the replay at
step 2 of 11, and it cost four modules instead of a venue adapter, a signing
path, a paper engine, a persistence schema, a dashboard API and a themed UI
built on an edge nobody had measured.

This is that test for the round table, and it exists because the fund shipped a
self-evolution mechanism it has never checked. `roundtable.calibration.
seat_weights` grades every seat and down-weights the ones with a worse record.
Whether that HELPS is unmeasured — and the mechanism is weaker than it looks:
`weighted_tally` multiplies only inside `RoundTable._fallback_consensus`, which
runs when the chair LLM fails. On the normal path the weight reaches the chair
as a sentence in a prompt ("this seat's calls have been better than average;
weight 1.23") and an LLM decides what to do with it. That is persuasion, not
arithmetic. A replay is the only way to find out which decides better.

WHAT THIS CAN ANSWER
    Anything downstream of what the seats said: seat weights, the tally rule,
    the chair-versus-vote question, a different neutral threshold.

WHAT IT CANNOT, AND MUST NOT APPEAR TO
    Anything that changes what the seats SAY. Prompt edits and the relevance
    scoping in `relevant_lesson_lines` alter the evidence block, so the seats
    would have said something else and there is nothing stored to replay.
    Measuring those needs real calls and real spend. Every verdict carries a
    `scope` line saying so, because a harness that blurred this would be worse
    than none: it would retire the question while leaving it open.

The comparison is PAIRED — both arms decide the same theses — so the test is
McNemar's, over the theses where the arms disagreed. Cases they agree on carry
no information about which is better, however many of them there are.

    python -m roundtable.replay        # run it against the live memory store
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from math import comb
from typing import Any, Optional

from roundtable.types import SeatOpinion, Signal, Thesis

logger = logging.getLogger(__name__)

# Below this there is no verdict to give. Matches the calibration module's bar:
# a decision rule judged on a handful of trades is judged on luck.
MIN_CASES = 30

# Two-sided, conventional. Stated as a constant so a disappointing result
# cannot be rescued by quietly moving it.
ALPHA = 0.05

SCOPE_NOTE = (
    "Replays DECISIONS, not deliberations: the seats' opinions are fixed as "
    "recorded. A change that alters the evidence block — a prompt edit, the "
    "lesson retrieval scope — would have changed what the seats said, and "
    "cannot be evaluated here at any sample size."
)


@dataclass(frozen=True)
class ReplayCase:
    """One resolved thesis, with everything needed to re-decide it."""

    thesis_id: str
    symbol: str
    asset_class: str
    opinions: tuple[dict, ...]
    chair_signal: Optional[str]
    chair_confidence: Optional[float]
    realized_return: float

    def live_opinions(self) -> int:
        """Seats that actually answered. An abstention is not a neutral vote —
        counting it as one lets a crashed API call outvote a seat."""
        return sum(1 for o in self.opinions if not o.get("failed"))

    def as_thesis(self) -> Thesis:
        """Rebuild enough of the Thesis for the ENGINE's own tally to run on it.

        Reconstructed rather than re-implemented on purpose. A replay that
        re-derived the vote rule would measure a model of the fund, and the two
        would drift apart on the first change to either.
        """
        return Thesis(
            thesis_id=self.thesis_id,
            symbol=self.symbol,
            asset_class=self.asset_class,
            opinions=[
                SeatOpinion(
                    seat_id=str(o.get("seat_id") or ""),
                    seat_name=str(o.get("seat_name") or o.get("seat_id") or ""),
                    signal=_signal(o.get("signal")),
                    confidence=float(o.get("confidence") or 0.0),
                    reasoning=str(o.get("reasoning") or ""),
                    failed=bool(o.get("failed")),
                )
                for o in self.opinions
            ],
        )

    def was_right(self, signal: Optional[str]) -> Optional[bool]:
        """Direction, not the sign of the return.

        A bearish call that was right shows a negative return; scoring on the
        raw sign would mark every correct short as a loss. `None` for a neutral
        call, which is not a trade and cannot be right or wrong.
        """
        if signal == "bullish":
            return self.realized_return > 0
        if signal == "bearish":
            return self.realized_return < 0
        return None


@dataclass
class ReplayArm:
    """One decision rule, scored over the same cases as every other arm."""

    name: str
    decisions: dict[str, Optional[str]] = field(default_factory=dict)
    outcomes: dict[str, Optional[bool]] = field(default_factory=dict)

    def decided_signal(self, case: ReplayCase) -> Optional[str]:
        return self.decisions.get(case.thesis_id)

    @property
    def cases(self) -> int:
        return len(self.decisions)

    @property
    def decided(self) -> int:
        """Directional calls. An arm that goes neutral on everything trades
        nothing, and its hit rate is a statement about an empty set."""
        return sum(1 for v in self.outcomes.values() if v is not None)

    @property
    def correct(self) -> int:
        return sum(1 for v in self.outcomes.values() if v is True)

    @property
    def hit_rate(self) -> Optional[float]:
        return round(self.correct / self.decided, 4) if self.decided else None

    def as_dict(self) -> dict:
        return {"name": self.name, "cases": self.cases, "decided": self.decided,
                "correct": self.correct, "hit_rate": self.hit_rate}


# ---------------------------------------------------------------- loading

def load_cases(memory: Any, limit: int = 500) -> list[ReplayCase]:
    """Resolved theses only — an open one has no outcome to score against."""
    try:
        delibs = memory.recent_deliberations(limit=limit)
        outcomes = {o["thesis_id"]: o for o in memory.resolved_outcomes(limit=limit)}
    except Exception:
        logger.exception("could not load replay cases")
        return []

    cases: list[ReplayCase] = []
    for row in delibs:
        outcome = outcomes.get(row.get("thesis_id"))
        if outcome is None or outcome.get("realized_return") is None:
            continue
        payload = row.get("payload") or {}
        consensus = payload.get("consensus") or {}
        cases.append(ReplayCase(
            thesis_id=row["thesis_id"],
            symbol=row.get("symbol") or "",
            asset_class=row.get("asset_class") or "equity",
            opinions=tuple(payload.get("opinions") or []),
            chair_signal=consensus.get("signal") or row.get("signal"),
            chair_confidence=consensus.get("confidence"),
            realized_return=float(outcome["realized_return"]),
        ))
    return cases


# ---------------------------------------------------------------- the arms

def chair_arm(cases: list[ReplayCase]) -> ReplayArm:
    """What actually happened. The baseline every variant has to beat."""
    arm = ReplayArm("chair (as it ran)")
    for c in cases:
        arm.decisions[c.thesis_id] = c.chair_signal
        arm.outcomes[c.thesis_id] = c.was_right(c.chair_signal)
    return arm


def weighted_arm(cases: list[ReplayCase], weights: dict[str, float]) -> ReplayArm:
    """A weighted vote of the same opinions, by the ENGINE's own rule.

    This is `RoundTable._fallback_consensus`'s decision, applied to every
    thesis rather than only to the ones where the chair fell over — which makes
    it the counterfactual: what if the vote count decided, and the chair only
    explained?
    """
    arm = ReplayArm(f"weighted vote ({'calibrated' if weights else 'equal'})")
    for c in cases:
        tally = c.as_thesis().weighted_tally(weights)
        bullish, bearish = tally["bullish"], tally["bearish"]
        signal: Optional[Signal] = (
            "bullish" if bullish > bearish else
            "bearish" if bearish > bullish else "neutral")
        arm.decisions[c.thesis_id] = signal
        arm.outcomes[c.thesis_id] = c.was_right(signal)
    return arm


# ---------------------------------------------------------------- comparing

def compare(a: ReplayArm, b: ReplayArm) -> dict:
    """Is b better than a, or is this noise?

    PAIRED, over the theses where the two disagreed. Agreements carry no
    information about which rule is better, so a thousand of them neither
    strengthen nor weaken the result — counting them is how a tiny real
    difference gets dressed up as a large sample.

    McNemar's exact test: under the null that the two rules are equally good,
    each disagreement is a fair coin. The two-sided p is the probability of a
    split at least this lopsided.
    """
    shared = set(a.outcomes) & set(b.outcomes)
    a_better = sum(1 for t in shared
                   if a.outcomes[t] is True and b.outcomes[t] is False)
    b_better = sum(1 for t in shared
                   if b.outcomes[t] is True and a.outcomes[t] is False)
    n = a_better + b_better

    verdict = {
        "a": a.as_dict(), "b": b.as_dict(),
        "cases": len(shared),
        "disagreements": n,
        "a_better": a_better, "b_better": b_better,
        "p_value": None,
        "distinguishable": False,
        "scope": SCOPE_NOTE,
        "reason": "",
    }

    if len(shared) < MIN_CASES:
        verdict["reason"] = (
            f"{len(shared)} resolved theses — {MIN_CASES} needed before a "
            f"decision rule is judged on anything but luck.")
        return verdict
    if n == 0:
        verdict["reason"] = (
            "The two rules decided every thesis identically. There is nothing "
            "to test; this is not evidence that they are equally good.")
        return verdict

    p = _mcnemar_exact(b_better, n)
    verdict["p_value"] = round(p, 5)
    verdict["distinguishable"] = p < ALPHA
    better = "b" if b_better > a_better else "a"
    verdict["reason"] = (
        f"{n} theses decided differently; {better} was right on "
        f"{max(a_better, b_better)} of them (p={p:.4f}). "
        + ("A real difference." if p < ALPHA else
           f"Not distinguishable from a coin at alpha={ALPHA}.")
    )
    return verdict


def _mcnemar_exact(successes: int, n: int) -> float:
    """Two-sided exact binomial at p=0.5. No SciPy — this is five lines and
    the dependency would be the larger cost."""
    if n == 0:
        return 1.0
    k = min(successes, n - successes)
    tail = sum(comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def _signal(raw: Any) -> Signal:
    return raw if raw in ("bullish", "bearish", "neutral") else "neutral"


# ---------------------------------------------------------------- CLI

def run(memory: Any) -> dict:
    """Both arms against the live store, with the comparison."""
    cases = load_cases(memory)
    baseline = chair_arm(cases)
    variant = weighted_arm(cases, _live_weights(memory))
    return {"cases": len(cases), "verdict": compare(baseline, variant)}


def _live_weights(memory: Any) -> dict[str, float]:
    from roundtable.calibration import score_seats, seat_weights
    try:
        delibs = memory.recent_deliberations(limit=500)
        outcomes = {o["thesis_id"]: o for o in memory.resolved_outcomes(limit=500)}
    except Exception:
        return {}
    return seat_weights(score_seats(delibs, outcomes).seats)


def _demo() -> None:
    """Self-check: a rule that is right whenever the other is wrong must be
    detected, and an even split must not be."""
    def case(tid, signals, chair, ret):
        return ReplayCase(tid, "AAPL", "equity",
                          tuple({"seat_id": f"s{i}", "signal": s, "confidence": 60.0,
                                 "failed": False} for i, s in enumerate(signals)),
                          chair, 60.0, ret)

    lopsided = [case(f"t{i}", ["bearish"], "bullish", -0.05) for i in range(MIN_CASES)]
    v = compare(chair_arm(lopsided), weighted_arm(lopsided, {}))
    assert v["b_better"] == MIN_CASES and v["a_better"] == 0, v
    assert v["distinguishable"], v

    split = ([case(f"w{i}", ["bearish"], "bullish", -0.05) for i in range(15)]
             + [case(f"l{i}", ["bearish"], "bullish", 0.05) for i in range(15)])
    v = compare(chair_arm(split), weighted_arm(split, {}))
    assert v["a_better"] == v["b_better"] == 15, v
    assert not v["distinguishable"], v

    # A neutral call is never scored as a win.
    neutral = [case(f"n{i}", ["bullish", "bearish"], "neutral", 0.05)
               for i in range(MIN_CASES)]
    assert chair_arm(neutral).hit_rate is None
    print("replay self-check passed")


if __name__ == "__main__":
    import json
    import sys

    if "--demo" in sys.argv:
        _demo()
        raise SystemExit(0)

    from memory.store import MemoryStore
    print(json.dumps(run(MemoryStore()), indent=2))
