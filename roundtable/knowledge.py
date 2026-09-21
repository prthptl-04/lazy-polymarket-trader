"""Where a piece of evidence came from, and when it was true.

The shape is QuantMind's (LLMQuant/quant-mind, MIT): typed knowledge carries
"its own text, an `as_of` timestamp and a light source ref", so an artifact is
self-contained and can be queried by time. No upstream code is used — the
principle is what transfers, and it transfers because this repo had a hole
shaped exactly like it.

The hole: a stored deliberation kept the opinions, the consensus and the tally
and threw away the evidence. You could see what the committee decided and never
what it was looking at. For a fund that is the wrong half to keep — the verdict
is the cheap part, the inputs are what you argue with six months later.

**Undated means stale.** The default has to be pessimistic, because an unknown
timestamp rendered as current is a lie the seats have no way to catch, and the
cost is asymmetric: discounting fresh evidence loses one cycle's conviction,
while trusting stale evidence sizes a position against a market that has moved.

**Derived evidence is exempt.** Technicals computed from this cycle's bars are
exactly as old as the bars, which carry their own ref. Warning twice about one
staleness trains the seats to ignore the warning.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

# Evidence older than this is announced as stale in the block the seats read.
# One hour: long enough that a 5-minute cycle never flags its own fetches,
# short enough that a provider stuck since premarket is caught before the open.
STALE_AFTER_SECONDS = 3600.0


@dataclass(frozen=True)
class SourceRef:
    """One evidence block's provenance.

    Block-level rather than per-line on purpose: every headline in one fetch
    shares a timestamp, and a per-line `as_of` would be the same number
    repeated eight times with more code to keep in step.
    """

    kind: str                       # news | catalysts | technicals | corroboration | ...
    source: str                     # "Massive /v2/reference/news", "SEC EDGAR", "computed"
    as_of: Optional[float] = None   # epoch seconds when this was true
    # Computed from other evidence in this same cycle, so it has no independent
    # age and must not raise a second staleness warning.
    derived: bool = False

    def age_seconds(self, now: Optional[float] = None) -> Optional[float]:
        if self.as_of is None:
            return None
        return max(0.0, (now if now is not None else time.time()) - self.as_of)

    def is_stale(self, now: Optional[float] = None) -> bool:
        if self.derived:
            return False
        age = self.age_seconds(now)
        if age is None:
            return True             # unknown age is not an argument for trusting it
        return age > STALE_AFTER_SECONDS

    def describe(self, now: Optional[float] = None) -> str:
        if self.derived:
            return f"{self.kind}: computed this cycle from the evidence above"
        age = self.age_seconds(now)
        if age is None:
            return f"{self.kind}: {self.source} (age unknown — treat as STALE)"
        label = f"{self.kind}: {self.source} ({_human(age)} ago)"
        return f"{label} — STALE" if self.is_stale(now) else label

    def as_dict(self) -> dict:
        return {"kind": self.kind, "source": self.source,
                "as_of": self.as_of, "derived": self.derived}


def _human(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.0f}h"
    return f"{seconds / 86400:.0f}d"


def _demo() -> None:
    now = 1_000_000.0
    assert SourceRef("news", "s", now - 60).age_seconds(now) == 60
    assert not SourceRef("news", "s", now - 60).is_stale(now)
    assert SourceRef("news", "s", None).is_stale(now), "undated must be stale"
    assert not SourceRef("tech", "computed", None, derived=True).is_stale(now)
    assert "STALE" in SourceRef("news", "s", now - 99999).describe(now)
    assert _human(3600) == "60m" and _human(86400) == "24h"
    print("knowledge self-check passed")


if __name__ == "__main__":
    _demo()
