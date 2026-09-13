"""Do the seats actually disagree?

The round table costs six model calls per candidate and its entire economic
justification is that the seats are independent. Nothing has ever checked.

This is the highest-value statistic available to this fund today, because it
needs NO outcomes — only the opinions already persisted in
`deliberations.payload`. Every other measure of the committee waits on resolved
trades; this one can be computed at the current sample.

Cohen's κ rather than raw agreement, because raw agreement is mostly chance:
two seats that each say "bullish" 90% of the time out of habit agree 82% of the
time while sharing no information at all. κ removes exactly that.

    κ = (po − pe) / (1 − pe)

κ ≈ 0 is independence. κ → 1 is one opinion with two voices. If the "independent"
seats sit above ~0.7 the committee is not a committee, the effective sample of
the whole track record is far smaller than the trade count, and six calls per
candidate buy no diversification.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping, Optional, Sequence

# Below this the pairwise rate is a handful of coin flips.
MIN_DELIBERATIONS_FOR_KAPPA = 10

# Above this, two seats are effectively one voice.
DUPLICATE_THRESHOLD = 0.7


@dataclass(frozen=True)
class PairAgreement:
    a: str
    b: str
    n: int                          # deliberations where BOTH seats responded
    agreement_pct: float
    kappa: Optional[float]
    reason: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "a": self.a, "b": self.b, "n": self.n,
            "agreement_pct": round(self.agreement_pct, 1),
            "kappa": None if self.kappa is None else round(self.kappa, 3),
            "reason": self.reason,
            "duplicate": self.kappa is not None and self.kappa >= DUPLICATE_THRESHOLD,
        }


def pairwise_agreement(deliberations: Sequence[Mapping[str, Any]]) -> dict:
    """κ for every pair of seats, over deliberations where both spoke."""
    calls: dict[str, dict[int, str]] = {}
    for i, row in enumerate(deliberations):
        payload = row.get("payload") or {}
        for op in payload.get("opinions", []):
            if op.get("failed"):
                continue            # an abstention is not a disagreement
            signal = op.get("signal")
            seat = op.get("seat_id")
            if seat and signal:
                calls.setdefault(seat, {})[i] = signal

    pairs: list[PairAgreement] = []
    for a, b in combinations(sorted(calls), 2):
        shared = sorted(set(calls[a]) & set(calls[b]))
        n = len(shared)
        if n == 0:
            continue
        agree = sum(1 for i in shared if calls[a][i] == calls[b][i])
        po = agree / n
        pairs.append(PairAgreement(
            a=a, b=b, n=n, agreement_pct=po * 100,
            **_kappa(po, [calls[a][i] for i in shared], [calls[b][i] for i in shared]),
        ))

    scored = [p for p in pairs if p.kappa is not None]
    return {
        "pairs": [p.as_dict() for p in sorted(pairs, key=lambda p: -(p.kappa or -9))],
        "n_deliberations": len(deliberations),
        "mean_kappa": round(sum(p.kappa for p in scored) / len(scored), 3) if scored else None,
        "mean_agreement_pct": (
            round(sum(p.agreement_pct for p in pairs) / len(pairs), 1) if pairs else None
        ),
        "duplicates": [p.as_dict() for p in scored if p.kappa >= DUPLICATE_THRESHOLD],
        "reason": (
            f"{len(deliberations)} deliberations — below {MIN_DELIBERATIONS_FOR_KAPPA}, "
            "these rates are a handful of coin flips"
            if len(deliberations) < MIN_DELIBERATIONS_FOR_KAPPA else None
        ),
    }


def _kappa(po: float, left: Sequence[str], right: Sequence[str]) -> dict:
    """Chance-corrected agreement from each seat's own marginals.

    A constant seat makes pe = 1 and κ undefined. Returning raw agreement there
    and calling it κ would report the most duplicated seat on the table as the
    most independent one.
    """
    n = len(left)
    labels = set(left) | set(right)
    pe = sum((left.count(l) / n) * (right.count(l) / n) for l in labels)
    if pe >= 1.0:
        return {"kappa": None, "reason": "one seat never varies its call; κ is undefined"}
    return {"kappa": (po - pe) / (1 - pe), "reason": None}
