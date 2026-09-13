"""Fact corroboration — a second source for the numbers the seats reason from.

The gap this fills: the Devil's Advocate attacks *reasoning*, and nothing
attacked the *facts*. Every seat reasons from one evidence block built from one
provider. If that provider is stale, wrong, or returns a different symbol's
data, five independent seats reach five confident conclusions from the same bad
number and the transcript looks like agreement.

So this fetches the same quantities from an independent source and compares
them. Agreement or disagreement is arithmetic — no LLM judgement, no prompt,
no tokens. The Corroborator *seat* then interprets what the mismatches mean;
this module only establishes what they are.

Design notes:
- Tolerances are per-field, not global. A 1% price gap between a 15-min delayed
  feed and a live venue quote is normal; a 1% gap in shares outstanding is a
  different company.
- A field missing from one source is `unverified`, not `mismatched`. Absence is
  not disagreement, and conflating them would cry wolf on every thin symbol.
- A mismatch is a flag, not a veto. The fund does not skip a trade because two
  vendors round differently; it tells the table so a seat can weigh it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Per-field relative tolerance. Price feeds legitimately differ; identity
# fields (share count) do not.
DEFAULT_TOLERANCES: dict[str, float] = {
    "price": 0.02,          # 2% — delayed feed vs live venue quote
    "atr": 0.15,            # ATR depends on bar source and window alignment
    "volume": 0.25,         # consolidated vs venue-only tape differ a lot
    "market_cap": 0.05,
    "shares_outstanding": 0.01,
}
FALLBACK_TOLERANCE = 0.05


@dataclass(frozen=True)
class FieldCheck:
    field: str
    primary: Optional[float]
    secondary: Optional[float]
    tolerance: float

    @property
    def status(self) -> str:
        if self.primary is None or self.secondary is None:
            return "unverified"
        return "agree" if self.within_tolerance else "mismatch"

    @property
    def relative_gap(self) -> Optional[float]:
        if self.primary is None or self.secondary is None:
            return None
        scale = max(abs(self.primary), abs(self.secondary))
        if scale == 0:
            return 0.0
        return abs(self.primary - self.secondary) / scale

    @property
    def within_tolerance(self) -> bool:
        gap = self.relative_gap
        return gap is not None and gap <= self.tolerance

    def describe(self) -> str:
        if self.status == "unverified":
            missing = "primary" if self.primary is None else "secondary"
            return f"{self.field}: unverified ({missing} source has no value)"
        if self.status == "agree":
            return f"{self.field}: agree ({self.primary:.4g} vs {self.secondary:.4g})"
        return (f"{self.field}: MISMATCH {self.primary:.4g} vs {self.secondary:.4g} "
                f"({self.relative_gap:.1%} apart, tolerance {self.tolerance:.0%})")


@dataclass
class CorroborationReport:
    symbol: str
    checks: list[FieldCheck] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def mismatches(self) -> list[FieldCheck]:
        return [c for c in self.checks if c.status == "mismatch"]

    @property
    def unverified(self) -> list[FieldCheck]:
        return [c for c in self.checks if c.status == "unverified"]

    @property
    def agreed(self) -> list[FieldCheck]:
        return [c for c in self.checks if c.status == "agree"]

    @property
    def trustworthy(self) -> bool:
        """No contradictions AND at least one fact actually corroborated.

        Zero checks is not a pass. A symbol where every field came back
        unverified has been *checked against nothing*, and reporting that as
        clean is the failure this module exists to prevent.
        """
        return not self.mismatches and bool(self.agreed)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "trustworthy": self.trustworthy,
            "agreed": len(self.agreed),
            "mismatches": [c.describe() for c in self.mismatches],
            "unverified": [c.field for c in self.unverified],
            "notes": list(self.notes),
        }

    def evidence_lines(self) -> tuple[str, ...]:
        """Rendered into the Candidate so the seats see it as evidence."""
        if not self.checks:
            return ("Corroboration: not run.",)
        lines = [
            f"Corroboration: {len(self.agreed)} field(s) confirmed against a "
            f"second source, {len(self.mismatches)} mismatch(es), "
            f"{len(self.unverified)} unverified."
        ]
        lines.extend(f"  {c.describe()}" for c in self.mismatches)
        lines.extend(f"  {n}" for n in self.notes)
        if not self.agreed:
            lines.append(
                "  WARNING: nothing was independently confirmed — treat every "
                "figure below as single-sourced."
            )
        return tuple(lines)


def compare(
    symbol: str,
    primary: dict[str, Any],
    secondary: dict[str, Any],
    *,
    tolerances: Optional[dict[str, float]] = None,
    notes: Optional[list[str]] = None,
) -> CorroborationReport:
    """Compare two independently-fetched fact sets for the same symbol."""
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    fields = sorted(set(primary) | set(secondary))
    checks = [
        FieldCheck(
            field=f,
            primary=_num(primary.get(f)),
            secondary=_num(secondary.get(f)),
            tolerance=tol.get(f, FALLBACK_TOLERANCE),
        )
        for f in fields
    ]
    return CorroborationReport(symbol=symbol, checks=checks, notes=list(notes or []))


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):        # bool is an int subclass; never a measurement
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
