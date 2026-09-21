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

from roundtable.knowledge import SourceRef


def _excluded_seat_names(asset_class: str) -> str:
    """Imported lazily inside the function to avoid a seats<->types cycle."""
    from roundtable.seats import excluded_seats
    return ", ".join(s.name for s in excluded_seats(asset_class))


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
    # Dated events and insider flow (trading/catalysts.py). Separate from
    # sentiment because these are FACTS WITH DATES — a filing, a scheduled
    # print — while sentiment is what people are saying about them. Conflating
    # the two lets a loud opinion inherit a filing's credibility.
    catalyst_notes: tuple[str, ...] = ()
    # How this order will reach the market, and what that costs. Without it the
    # committee priced a round trip across a 189bps crypto book that we never
    # make — six seats reasoning correctly from a premise the block got wrong.
    execution_note: Optional[str] = None
    portfolio_notes: tuple[str, ...] = ()
    corroboration_notes: tuple[str, ...] = ()
    lessons: tuple[str, ...] = ()
    # What the committee has spent and earned. In the evidence block rather
    # than a system prompt, same as lessons: the system prompt carries the
    # cache tag (rule #2) and must stay byte-identical between deliberations.
    budget_notes: tuple[str, ...] = ()
    # Where each block above came from and when it was true. Empty renders
    # exactly as before, so every existing caller is unaffected.
    sources: tuple[SourceRef, ...] = ()

    @property
    def r_multiple(self) -> Optional[float]:
        if self.entry is None or self.stop is None or self.target is None:
            return None
        risk = abs(self.entry - self.stop)
        return abs(self.target - self.entry) / risk if risk else None

    def evidence_block(self, now: Optional[float] = None) -> str:
        """Rendered once per deliberation and sent as the variable message.

        `now` is injectable so the provenance section is deterministic in tests;
        production passes nothing and it reads the clock.
        """
        lines = [f"INSTRUMENT: {self.symbol} ({self.asset_class})",
                 f"Session: {self.session}",
                 f"Last price: {self.price}"]

        def add(label: str, value, suffix: str = "") -> None:
            if value is not None:
                lines.append(f"{label}: {value}{suffix}")

        if self.spread_bps is not None:
            lines.append(
                f"Spread: {self.spread_bps} bps — what this broker charges for "
                "immediacy right now, i.e. the cost of DEMANDING liquidity by "
                "crossing. It is not a measure of how deep the market is."
            )
        if self.execution_note:
            lines.append(f"Execution: {self.execution_note}")
        add("ATR", self.atr)
        add("CVaR (95%)", self.cvar_pct)
        if self.amihud_illiquidity is not None:
            lines.append(
                f"Amihud illiquidity: {self.amihud_illiquidity} — price impact "
                "per dollar traded in the UNDERLYING market, computed from bar "
                "returns and volumes. It measures DEPTH: how far our own order "
                "would move the price."
            )
        if self.spread_bps is not None and self.amihud_illiquidity is not None:
            # These two were read as contradictory in four live deliberations,
            # and the committee stood aside over it. They are not comparable:
            # a deep underlying market carrying a wide retail quote is exactly
            # what a market-maker-routed crypto venue is.
            lines.append(
                "  NOTE: the spread and the Amihud figure measure different "
                "things on different venues and DO NOT contradict each other. A "
                "deep market can carry a wide quoted spread — that is a broker "
                "markup, not evidence that the book is thin. Neither figure "
                "refutes the other; read the execution note for which of the "
                "two this order actually pays."
            )
        add("Altman Z", self.altman_z)
        add("Altman zone", self.altman_zone)
        add("Piotroski F", self.piotroski_f)
        add("Proposed entry", self.entry)
        add("Proposed stop", self.stop)
        add("Proposed target", self.target)
        add("Reward:risk", self.r_multiple)

        for header, notes in (
            ("SENTIMENT", self.sentiment_notes),
            ("CATALYSTS (dated events, filings, insider flow)", self.catalyst_notes),
            ("TECHNICALS", self.technical_notes),
            ("PORTFOLIO", self.portfolio_notes),
            ("CORROBORATION", self.corroboration_notes),
            ("LESSONS FROM PAST LOSSES (apply these)", self.lessons),
            ("WHAT THIS COMMITTEE COSTS TO RUN", self.budget_notes),
        ):
            if notes:
                lines.append(f"\n{header}:")
                lines.extend(f"  - {n}" for n in notes)

        if self.sources:
            # Last, not first: a seat should read the evidence and then learn
            # how much to trust it. Leading with provenance buries the content.
            lines.append("\nPROVENANCE (how old is what you just read):")
            lines.extend(f"  - {ref.describe(now)}" for ref in self.sources)
            if any(ref.is_stale(now) for ref in self.sources):
                lines.append(
                    "  Anything marked STALE may have been overtaken by the market. "
                    "Lower your confidence rather than assuming it still holds.")

        absent = _excluded_seat_names(self.asset_class)
        if absent:
            # A sparse vote must read as a smaller committee, not as weak
            # conviction. The seats that sit are told who is missing and why.
            lines.append(
                f"\nSEATS NOT CONSULTED on {self.asset_class}: {absent}. They have "
                "no mandate for this asset class and were not asked. The table is "
                "smaller than usual by design — do not read the absent seats as "
                "silent agreement or as caution."
            )

        if self.asset_class == "crypto":
            lines.append(
                "\nNOT APPLICABLE: Altman Z and Piotroski F are undefined for an "
                "asset with no issuer, no financial statements and no accruals. "
                "This is a category difference, not missing data — the "
                "balance-sheet seat should reason from liquidity, volatility "
                "regime and the technicals below, or say it has no mandate here."
            )

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
            "exit plan": self.stop,
        }
        if self.asset_class != "crypto":
            # For an equity these ARE defined, so absent is a real gap. For
            # crypto they are a category error and are reported as such above.
            checks["Altman Z"] = self.altman_z
            checks["Piotroski F"] = self.piotroski_f
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
    # What the seats were shown. Optional because a resumed or abandoned thesis
    # is rebuilt from a stored row rather than from a live candidate — but when
    # it is present the deliberation is a self-contained artifact rather than a
    # verdict whose inputs are gone.
    candidate: Optional[Candidate] = None

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

    @property
    def abstentions(self) -> int:
        return sum(1 for o in self.opinions if o.failed)

    @property
    def participation(self) -> float:
        """Fraction of the table that actually spoke.

        `MIN_RESPONDING_SEATS = 3` of 6 means a thesis built on half a table is
        quorate, and nothing downstream could tell it apart from one built on
        all six. That is how three truncated seats stayed invisible: the
        committee reported a neutral consensus, which reads as a decision
        rather than as an absence.
        """
        if not self.opinions:
            return 0.0
        return 1.0 - self.abstentions / len(self.opinions)

    def effective_confidence(self, consensus) -> float:
        """Stated conviction, scaled by how much of the table stood behind it.

        Conviction earned by six seats is not the same as conviction asserted
        by three. Scaled rather than discarded: a thin committee still knows
        something, it just knows it less certainly, and the pipeline sizes on
        this number.
        """
        if consensus is None:
            return 0.0
        return round(float(consensus.confidence or 0.0) * self.participation, 2)

    def weighted_tally(self, weights: dict[str, float]) -> dict[str, float]:
        """The vote count, scaled by each seat's demonstrated record.

        `tally()` stays the raw count — it is the transcript of who said what,
        and a transcript that silently re-weights itself is not one. This is
        the number a decision is made on.

        A seat with no entry in `weights` counts as 1.0: an unknown seat is not
        a discredited one.
        """
        counts = {s: 0.0 for s in VALID_SIGNALS}
        for o in self.opinions:
            if not o.failed:
                counts[o.signal] += float(weights.get(o.seat_id, 1.0))
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
        """The whole artifact: what was decided AND what it was decided from.

        `evidence` and `sources` were the missing half. A resolved thesis used
        to record the verdict and destroy the inputs, which makes a decision
        impossible to audit later and makes the deliberation un-replayable.
        """
        return {
            "evidence": self.candidate.evidence_block() if self.candidate else None,
            "sources": [r.as_dict() for r in self.candidate.sources] if self.candidate else [],
            "thesis_id": self.thesis_id,
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "created": self.created,
            "status": self.status,
            "opinions": [o.as_dict() for o in self.opinions],
            "consensus": self.consensus.as_dict() if self.consensus else None,
            "tally": self.tally(),
        }
