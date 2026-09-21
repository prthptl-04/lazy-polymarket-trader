"""Scoring the round table against what actually happened.

Without this the committee never learns. `trading.pipeline.CONFIDENCE_SHRINK`
is a judgement call — 0.5, chosen to be pessimistic — and it can only become a
measurement once theses have outcomes attached to them. This module is that
loop:

    resolve a thesis  →  score each seat  →  fit the shrink  →  size better

Three things it is careful about:

**A seat is scored on its own call, not the committee's.** A seat that voted
bearish inside a bullish committee that lost money was *right*, and its
scorecard should say so. Scoring seats by committee outcome would reward
conformity, which is the exact failure the Devil's Advocate exists to prevent.

**Brier, not accuracy.** Accuracy ignores confidence, so a seat that is right
55% of the time while claiming 95% certainty looks identical to one claiming
55%. Brier punishes confident wrongness, which is the behaviour that actually
costs money. Lower is better; 0.25 is the score of always saying 50%.

**A fit needs samples.** `fit_confidence_shrink` refuses below
`MIN_SAMPLES_FOR_FIT` and says so rather than returning a number derived from
four trades. An overfitted calibration is worse than the pessimistic constant
it replaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from finance.risk_metrics import brier_score


# Below this, a fitted shrink is noise dressed as evidence.
MIN_SAMPLES_FOR_FIT = 30

# And below this, a SEAT's own score is noise dressed as a verdict on that seat.
# The fund is long-only and `correct` is `realized > 0`, so every seat that
# voted bullish is scored on the identical event: per-seat differences at small
# n are noise by construction, not merely by sample size.
MIN_SAMPLES_FOR_SEAT_SCORE = 30

# Never let a fit recommend taking stated confidence at face value — hence the
# ceiling. The FLOOR is symmetric, and used to be 0.1, which made a whole class
# of measurement inexpressible: the one this strategy actually lives in.
#
# The fund's break-even hit rate is not 50%. Under the fixed 2xATR stop /
# 3xATR target geometry the payoff ratio is 1.5, so break-even is 1/(1+1.5) =
# 40%, and a profitable configuration wins 40-52%. Every one of those was
# pinned at the old floor, so the calibration could not tell a committee
# winning 40% from one winning 52% — and the floor was not even conservative:
# at a true p of 0.45 it forced p = 0.53, sizing 2.6x LARGER than the truth.
#
# A negative shrink is not nonsense. It says the committee is wrong more often
# when it is confident, which is real, measurable, and the single most
# important thing this loop could ever discover. Kelly does the refusing:
# below break-even `f*` is negative and `size_position` declines. A floor that
# made that unreachable meant the fund could never stop trading on the evidence
# of its own record, which is what the loop is FOR.
MAX_SHRINK = 0.9
MIN_SHRINK = -MAX_SHRINK

# The fund's payoff ratio, for reporting expectancy. A hit rate alone is
# unreadable when break-even is 40% rather than 50% — "realized 45%" looks like
# failure and is in fact +0.125R per trade.
REPORTING_PAYOFF_RATIO = 1.5


@dataclass(frozen=True)
class SeatScore:
    seat_id: str
    seat_name: str
    samples: int
    hit_rate: float               # fraction of calls whose direction was right
    brier: float                  # lower is better; 0.25 == always saying 50%
    mean_confidence: float
    overconfidence: float         # stated confidence minus realized hit rate
    abstentions: int = 0

    @property
    def is_scored(self) -> bool:
        """Enough calls for this seat's own record to mean anything."""
        return self.samples >= MIN_SAMPLES_FOR_SEAT_SCORE

    @property
    def is_calibrated(self) -> bool:
        """Within 10 points either way. Wider than that is a real bias.

        Gated at MIN_SAMPLES_FOR_SEAT_SCORE, not at samples > 0. With the old
        guard, three resolved theses could have the dashboard announce "5 of 6
        seats calibrated" — a clean bill of health issued on a handful of coin
        flips, in the panel about earning the right to trade real money.
        """
        return self.is_scored and abs(self.overconfidence) <= 10.0

    @property
    def beats_a_coin_flip(self) -> bool:
        """Unscored is not better than chance; it is unknown."""
        return self.is_scored and self.brier < 0.25

    def as_dict(self) -> dict:
        return {
            "seat_id": self.seat_id,
            "seat_name": self.seat_name,
            "samples": self.samples,
            "hit_rate": round(self.hit_rate, 4),
            "brier": round(self.brier, 4),
            "mean_confidence": round(self.mean_confidence, 2),
            "overconfidence": round(self.overconfidence, 2),
            "abstentions": self.abstentions,
            "calibrated": self.is_calibrated,
            "beats_coin_flip": self.beats_a_coin_flip,
            "scored": self.is_scored,
            "min_samples": MIN_SAMPLES_FOR_SEAT_SCORE,
        }


@dataclass(frozen=True)
class ShrinkFit:
    shrink: Optional[float]
    samples: int
    realized_hit_rate: Optional[float]
    mean_confidence: Optional[float]
    reason: str

    @property
    def usable(self) -> bool:
        return self.shrink is not None


@dataclass
class Scorecard:
    """Per-seat and committee-level scores over resolved theses."""

    seats: list[SeatScore] = field(default_factory=list)
    committee: Optional[SeatScore] = None
    resolved: int = 0

    def as_dict(self) -> dict:
        return {
            "resolved": self.resolved,
            "committee": self.committee.as_dict() if self.committee else None,
            "seats": [s.as_dict() for s in self.seats],
        }

    def worst_calibrated(self) -> Optional[SeatScore]:
        scored = [s for s in self.seats if s.samples]
        return max(scored, key=lambda s: abs(s.overconfidence)) if scored else None


def direction_was_right(signal: str, realized_return: float) -> Optional[bool]:
    """Did the call's direction match the move?

    A neutral call has no direction to be right about, so it is excluded from
    scoring entirely rather than counted as a loss — punishing 'I don't know'
    teaches seats to guess.
    """
    if signal == "bullish":
        return realized_return > 0
    if signal == "bearish":
        return realized_return < 0
    return None


def score_seats(
    deliberations: Sequence[dict],
    outcomes: dict[str, dict],
) -> Scorecard:
    """Score every seat over theses that have a recorded outcome.

    `deliberations` are rows from `MemoryStore.recent_deliberations`;
    `outcomes` maps thesis_id → row from `MemoryStore.resolved_outcomes`.
    """
    per_seat: dict[str, dict[str, Any]] = {}
    committee_preds: list[float] = []
    committee_hits: list[int] = []
    resolved = 0

    for row in deliberations:
        outcome = outcomes.get(row.get("thesis_id"))
        if outcome is None or outcome.get("realized_return") is None:
            continue
        realized = float(outcome["realized_return"])
        resolved += 1

        payload = row.get("payload") or {}
        for opinion in payload.get("opinions", []):
            bucket = per_seat.setdefault(
                opinion["seat_id"],
                {"name": opinion.get("seat_name", opinion["seat_id"]),
                 "preds": [], "hits": [], "confs": [], "abstentions": 0},
            )
            if opinion.get("failed"):
                bucket["abstentions"] += 1
                continue
            # Scored on the seat's OWN call, not the committee's.
            right = direction_was_right(opinion.get("signal", ""), realized)
            if right is None:
                continue
            confidence = float(opinion.get("confidence") or 0.0)
            bucket["preds"].append(confidence / 100.0)
            bucket["hits"].append(1 if right else 0)
            bucket["confs"].append(confidence)

        consensus = payload.get("consensus") or {}
        c_right = direction_was_right(consensus.get("signal", ""), realized)
        if c_right is not None:
            committee_preds.append(float(consensus.get("confidence") or 0.0) / 100.0)
            committee_hits.append(1 if c_right else 0)

    seats = [
        _build_score(seat_id, bucket)
        for seat_id, bucket in sorted(per_seat.items())
    ]
    committee = (
        _build_score("committee", {
            "name": "Committee", "preds": committee_preds,
            "hits": committee_hits,
            "confs": [p * 100 for p in committee_preds], "abstentions": 0,
        })
        if committee_preds else None
    )
    return Scorecard(seats=seats, committee=committee, resolved=resolved)


def fit_confidence_shrink(
    outcomes: Iterable[dict],
    *,
    min_samples: int = MIN_SAMPLES_FOR_FIT,
) -> ShrinkFit:
    """Fit the confidence→probability shrink from realized results.

    Solves for the `s` that maps mean stated confidence onto realized hit rate:

        realized = 0.5 + (mean_confidence/100 − 0.5) × s

    Crude by design — a single global scalar, not a per-seat isotonic fit. It
    is enough to stop the fund sizing on systematically overstated confidence,
    and it is honest about needing samples before it says anything at all.
    """
    rows = [
        o for o in outcomes
        if o.get("confidence") is not None and o.get("correct") is not None
    ]
    if len(rows) < min_samples:
        return ShrinkFit(
            None, len(rows), None, None,
            f"need {min_samples} resolved theses to fit; have {len(rows)}. "
            "Until then the pessimistic constant stands.",
        )

    mean_conf = sum(float(r["confidence"]) for r in rows) / len(rows)
    realized = sum(1 for r in rows if r["correct"]) / len(rows)

    spread = mean_conf / 100.0 - 0.5
    if abs(spread) < 1e-6:
        return ShrinkFit(
            None, len(rows), realized, mean_conf,
            "stated confidence averages 50%, so there is no signal to scale",
        )

    raw = (realized - 0.5) / spread
    shrink = max(MIN_SHRINK, min(MAX_SHRINK, raw))
    note = ""
    if raw != shrink:
        note = f" (clamped from {raw:.2f})"

    # Expectancy, in R, so the number is readable against the right reference.
    b = REPORTING_PAYOFF_RATIO
    expectancy = realized * b - (1 - realized)
    verdict = "above" if realized > 1 / (1 + b) else "below"
    return ShrinkFit(
        shrink, len(rows), realized, mean_conf,
        f"fitted over {len(rows)} theses: mean confidence {mean_conf:.0f}, "
        f"realized hit rate {realized:.0%} ({expectancy:+.2f}R per trade, "
        f"{verdict} the {1 / (1 + b):.0%} break-even at {b}R) → "
        f"shrink {shrink:.2f}{note}",
    )


def _build_score(seat_id: str, bucket: dict) -> SeatScore:
    preds, hits, confs = bucket["preds"], bucket["hits"], bucket["confs"]
    n = len(hits)
    if not n:
        return SeatScore(
            seat_id=seat_id, seat_name=bucket["name"], samples=0,
            hit_rate=0.0, brier=0.0, mean_confidence=0.0, overconfidence=0.0,
            abstentions=bucket["abstentions"],
        )
    hit_rate = sum(hits) / n
    mean_conf = sum(confs) / len(confs)
    return SeatScore(
        seat_id=seat_id,
        seat_name=bucket["name"],
        samples=n,
        hit_rate=hit_rate,
        brier=brier_score(preds, hits),
        mean_confidence=mean_conf,
        overconfidence=mean_conf - hit_rate * 100.0,
        abstentions=bucket["abstentions"],
    )
