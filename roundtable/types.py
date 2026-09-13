"""Round-table data types.

The governing principle, and the one thing worth preserving from the reference
implementations: **compute the numbers deterministically, let the LLM only
interpret them.** A `Candidate` arrives carrying hard values — Altman Z,
Piotroski F, ATR, CVaR, spread, the live PDT budget — that were calculated in
`finance/` before any seat was consulted. Seats reason *about* those numbers.
They never invent them, and nothing they say changes them.

That is what keeps a confident-sounding paragraph from becoming a position
size.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional


Signal = Literal["bullish", "bearish", "neutral"]
VALID_SIGNALS: tuple[Signal, ...] = ("bullish", "bearish", "neutral")


@dataclass(frozen=True)
class Candidate:
    """Everything the table is allowed to know about one instrument."""

    symbol: str
    asset_class: str = "equity"
    price: float = 0.0
    session: str = "regular"

    # Deterministic evidence, computed before the table convenes.
    spread_bps: Optional[int] = None
    atr: Optional[float] = None
    cvar_pct: Optional[float] = None
    amihud_illiquidity: Optional[float] = None
    altman_z: Optional[float] = None
    altman_zone: Optional[str] = None
    piotroski_f: Optional[int] = None

    # Proposed exit plan (finance.exits). The Risk seat checks it; it is not
    # the table's job to invent one.
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None

    # Context the seats need but must not confuse with evidence.
    sentiment_notes: tuple[str, ...] = ()
    technical_notes: tuple[str, ...] = ()
    portfolio_notes: tuple[str, ...] = ()
    corroboration_notes: tuple[str, ...] = ()
    lessons: tuple[str, ...] = ()

    @property
    def r_multiple(self) -> Optional[float]:
        if self.entry is None or self.stop is None or self.target is None:
            return None
        risk = abs(self.entry - self.stop)
        return abs(self.target - self.entry) / risk if risk else None

    def evidence_block(self) -> str:
        """Rendered once per deliberation and sent as the variable message."""
        lines = [f"INSTRUMENT: {self.symbol} ({self.asset_class})",
                 f"Session: {self.session}",
                 f"Last price: {self.price}"]

        def add(label: str, value, suffix: str = "") -> None:
            if value is not None:
                lines.append(f"{label}: {value}{suffix}")

        add("Spread", self.spread_bps, " bps")
        add("ATR", self.atr)
        add("CVaR (95%)", self.cvar_pct)
        add("Amihud illiquidity", self.amihud_illiquidity)
        add("Altman Z", self.altman_z)
        add("Altman zone", self.altman_zone)
        add("Piotroski F", self.piotroski_f)
        add("Proposed entry", self.entry)
        add("Proposed stop", self.stop)
        add("Proposed target", self.target)
        add("Reward:risk", self.r_multiple)

        for header, notes in (
            ("SENTIMENT", self.sentiment_notes),
            ("TECHNICALS", self.technical_notes),
            ("PORTFOLIO", self.portfolio_notes),
            ("CORROBORATION", self.corroboration_notes),
            ("LESSONS FROM PAST LOSSES (apply these)", self.lessons),
        ):
            if notes:
                lines.append(f"\n{header}:")
                lines.extend(f"  - {n}" for n in notes)

        missing = self._missing_fields()
        if missing:
            lines.append(
                "\nNOT AVAILABLE (do not speculate about these — say so instead): "
                + ", ".join(missing)
            )
        return "\n".join(lines)

    def _missing_fields(self) -> list[str]:
        checks = {
            "spread": self.spread_bps, "ATR": self.atr, "CVaR": self.cvar_pct,
            "Altman Z": self.altman_z, "Piotroski F": self.piotroski_f,
            "exit plan": self.stop,
        }
        return [name for name, value in checks.items() if value is None]


@dataclass(frozen=True)
class SeatOpinion:
    seat_id: str
    seat_name: str
    signal: Signal
    confidence: float                 # 0-100
    reasoning: str
    key_points: tuple[str, ...] = ()
    concerns: tuple[str, ...] = ()
    failed: bool = False              # True when the seat's call errored out
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "seat_id": self.seat_id,
            "seat_name": self.seat_name,
            "signal": self.signal,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "key_points": list(self.key_points),
            "concerns": list(self.concerns),
            "failed": self.failed,
            "error": self.error,
        }


@dataclass(frozen=True)
class Consensus:
    signal: Signal
    confidence: float
    summary: str
    transcript: str
    dissent: str = ""
    synthesized_by_llm: bool = True   # False when we fell back to a vote count

    def as_dict(self) -> dict:
        return {
            "signal": self.signal,
            "confidence": self.confidence,
            "summary": self.summary,
            "transcript": self.transcript,
            "dissent": self.dissent,
            "synthesized_by_llm": self.synthesized_by_llm,
        }


@dataclass
class Thesis:
    """One full deliberation. Persisted so a STOP mid-debate is resumable."""

    symbol: str
    asset_class: str = "equity"
    thesis_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created: float = field(default_factory=time.time)
    opinions: list[SeatOpinion] = field(default_factory=list)
    consensus: Optional[Consensus] = None
    status: str = "in_progress"

    @property
    def signal(self) -> Optional[Signal]:
        return self.consensus.signal if self.consensus else None

    @property
    def confidence(self) -> Optional[float]:
        return self.consensus.confidence if self.consensus else None

    def tally(self) -> dict[str, int]:
        """Vote count across seats that actually produced an opinion."""
        counts = {s: 0 for s in VALID_SIGNALS}
        for o in self.opinions:
            if not o.failed:
                counts[o.signal] += 1
        return counts

    def has_dissent(self) -> bool:
        """True when the seats did not all agree.

        Unanimity in a six-seat LLM panel is a warning sign, not a green
        light — it usually means the seats saw the same framing rather than
        that the trade is safe.
        """
        counts = self.tally()
        return sum(1 for n in counts.values() if n > 0) > 1

    def as_payload(self) -> dict:
        return {
            "thesis_id": self.thesis_id,
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "created": self.created,
            "status": self.status,
            "opinions": [o.as_dict() for o in self.opinions],
            "consensus": self.consensus.as_dict() if self.consensus else None,
            "tally": self.tally(),
        }
