"""Post-mortem — what the committee should have seen.

Closing a losing position is the only moment the fund learns anything for
certain. Everything before it is opinion; the exit is a fact. This turns that
fact into a lesson the seats read on their next deliberation.

**Deterministic by design.** Every finding here is derived by comparing what the
seats said against what happened — no LLM, no tokens, no chance of inventing a
moral. An LLM asked "why did this lose?" will always produce a confident story;
arithmetic produces a finding only when one exists.

The findings it can actually establish:

- **Unanimity that was wrong.** The table treats unanimity as a caution flag;
  a unanimous loss is the evidence for that, and worth saying out loud.
- **A correct dissent.** If the Devil's Advocate (or any seat) argued the other
  side and the outcome matched, that seat's objection deserved more weight.
  This is the single most valuable thing to learn, because it is the failure
  mode the seat exists to prevent.
- **Trading on unverified data.** If the Corroborator flagged a mismatch or
  reported nothing confirmed, and we traded anyway, the lesson is about process
  rather than about the stock.
- **Overconfidence.** High stated confidence on a loss is a calibration fault,
  not a bad-luck story.

Lessons are written under `agent_id='*'` so every seat sees them, and injected
as **evidence**, never into the system prompts — those are cache-tagged per
CLAUDE.md #2, and mutating them would bust the prompt cache on every new lesson.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from roundtable.calibration import MIN_SAMPLES_FOR_FIT

logger = logging.getLogger(__name__)

# Stated confidence above this on a loss is a calibration fault worth recording.
OVERCONFIDENCE_THRESHOLD = 75.0
# Lessons older than this stop being shown; markets change and a stale rule is
# worse than none.
MAX_LESSONS_SHOWN = 6


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str
    severity: str = "note"          # note | warning


@dataclass
class Postmortem:
    """Analyses a closed position and records what to do differently."""

    memory: Any = None

    def analyse(
        self,
        *,
        symbol: str,
        realized_return: float,
        thesis: Optional[dict],
        exit_reason: str = "stop",
        plan: Optional[dict] = None,
    ) -> list[Finding]:
        """Findings for one closed position. Empty when nothing is learnable."""
        findings: list[Finding] = []
        if realized_return >= 0:
            # Wins are not automatically right, but there is nothing here we can
            # establish from a win alone. Claiming otherwise teaches superstition.
            return findings

        payload = (thesis or {}).get("payload") or thesis or {}
        opinions = payload.get("opinions") or []
        consensus = payload.get("consensus") or {}
        tally = payload.get("tally") or {}

        live = [o for o in opinions if not o.get("failed")]
        loss_pct = abs(realized_return) * 100

        # 1. Unanimity that turned out wrong.
        directional = {s: n for s, n in tally.items() if s != "neutral" and n}
        if len(directional) == 1 and sum(directional.values()) >= 2:
            findings.append(Finding(
                "unanimous_loss",
                f"{symbol}: all {sum(directional.values())} seats agreed and the "
                f"position lost {loss_pct:.1f}%. Unanimity was not corroboration — "
                "the seats likely shared one framing rather than checking it.",
                "warning",
            ))

        # 2. A dissent that was right. The most valuable finding available.
        consensus_signal = consensus.get("signal")
        for o in live:
            if o.get("signal") and o["signal"] != consensus_signal and o["signal"] != "neutral":
                findings.append(Finding(
                    "correct_dissent",
                    f"{symbol}: {o['seat_name']} dissented ({o['signal']}) against a "
                    f"{consensus_signal} consensus and was right. Its stated concern was: "
                    f"{(o.get('reasoning') or '').strip()[:200]}",
                    "warning",
                ))

        # 3. We traded on data nobody verified.
        corroborator = next((o for o in live if o.get("seat_id") == "corroborator"), None)
        if corroborator:
            text = " ".join([
                corroborator.get("reasoning") or "",
                " ".join(corroborator.get("concerns") or []),
            ]).lower()
            if "mismatch" in text or "single-sourced" in text or "unverified" in text:
                findings.append(Finding(
                    "traded_on_unverified_data",
                    f"{symbol}: the Corroborator flagged unverified or conflicting "
                    "figures and the trade went ahead anyway. Treat that flag as a "
                    "size reduction, not a footnote.",
                    "warning",
                ))

        # 4. Confidence that outran the evidence.
        confidence = consensus.get("confidence")
        if confidence and confidence >= OVERCONFIDENCE_THRESHOLD:
            findings.append(Finding(
                "overconfident_loss",
                f"{symbol}: the committee was {confidence:.0f}% confident and lost "
                f"{loss_pct:.1f}%. Confidence that high needs evidence that complete.",
            ))

        # 5. A stop that was plausibly too tight — but ONLY where the arithmetic
        #    can show it.
        #
        #    This used to fire on EVERY stopped-out bullish position with the
        #    text "the stop may have been sized to noise". It knew nothing of
        #    the kind: not the ATR multiple, not the post-exit path. And because
        #    these lines are injected into every subsequent deliberation's
        #    evidence block, a run of ordinary stop-outs taught the whole
        #    committee to widen stops — turning a planned 2R loss into a 6R one
        #    on the strength of something the fund never observed.
        #
        #    A stop at 2×ATR that was hit is a stop working. The two cases that
        #    are genuinely learnable:
        #      - the stop sat inside one ATR, i.e. inside a normal day's range;
        #      - the loss overshot the planned stop, i.e. a gap or slippage,
        #        which is a sizing assumption broken rather than a thesis wrong.
        if exit_reason == "stop" and consensus_signal == "bullish":
            finding = self._stop_quality(symbol, loss_pct, plan)
            if finding is not None:
                findings.append(finding)

        return findings

    @staticmethod
    def _stop_quality(symbol: str, loss_pct: float, plan: Optional[dict]) -> Optional[Finding]:
        """A finding only where the numbers support one. Silence otherwise —
        this module's own contract is that arithmetic produces a finding only
        when one exists."""
        if not plan:
            return None
        entry, stop, atr = plan.get("entry"), plan.get("stop"), plan.get("atr")
        if not entry or not stop or not atr or atr <= 0:
            return None

        distance = abs(entry - stop)
        multiple = distance / atr
        planned_loss_pct = distance / entry * 100

        if multiple < 1.0:
            return Finding(
                "stop_inside_noise",
                f"{symbol}: the stop sat {multiple:.1f}×ATR from entry — inside a "
                f"normal day's range. It was reached for {loss_pct:.1f}%, which the "
                "tape would have done whether or not the thesis was wrong.",
            )
        # A 25% overshoot is past rounding and into a gap or a bad fill.
        if loss_pct > planned_loss_pct * 1.25:
            return Finding(
                "stop_overshot",
                f"{symbol}: lost {loss_pct:.1f}% against a planned "
                f"{planned_loss_pct:.1f}% stop at {multiple:.1f}×ATR. The exit did "
                "not happen where the plan assumed, so the position was sized "
                "against a risk that was never the real one.",
            )
        return None

    def record(self, findings: list[Finding], *, symbol: str) -> int:
        """Persist findings as lessons every seat will read. Returns the count."""
        if not findings or self.memory is None:
            return 0
        written = 0
        for f in findings:
            try:
                self.memory.record_lesson(
                    "*", f.detail, context={"code": f.code, "symbol": symbol,
                                            "severity": f.severity},
                )
                written += 1
            except Exception:
                logger.exception("could not record lesson %s", f.code)
        return written

    def run(self, *, symbol: str, realized_return: float,
            thesis: Optional[dict], exit_reason: str = "stop",
            plan: Optional[dict] = None) -> list[Finding]:
        findings = self.analyse(symbol=symbol, realized_return=realized_return,
                                thesis=thesis, exit_reason=exit_reason, plan=plan)
        self.record(findings, symbol=symbol)
        return findings


def recent_lesson_lines(memory: Any, limit: int = MAX_LESSONS_SHOWN) -> tuple[str, ...]:
    """Lessons rendered for the evidence block.

    Goes to the seats as *evidence*, not as a system-prompt edit — the system
    blocks are cache-tagged, so mutating them would discard the prompt cache
    every time the fund learns something.

    GATED ON EVIDENCE. Recording is unchanged — findings are still written,
    audited and shown to a human on the lessons panel. What is gated is the
    INJECTION into future deliberations, because the asymmetry is brutal: a
    lesson that is noise persists and compounds across every subsequent debate,
    while a lesson withheld costs one cycle of un-learned insight.

    This is not hypothetical. An earlier version of this loop fired "the stop
    may have been sized to noise" on every stopped-out long, those lines were
    injected into every later deliberation, and the committee learned to widen
    stops on evidence the fund had never observed — turning a planned 2R loss
    into a larger one. Below MIN_SAMPLES_FOR_FIT resolved theses the fund says
    plainly that it has not learned anything yet.
    """
    if memory is None:
        return ()
    try:
        resolved = len(memory.resolved_outcomes(limit=MIN_SAMPLES_FOR_FIT))
    except Exception:
        # Cannot establish the sample → inject nothing. The default is "the
        # fund has not learned anything", never "carry on with the table".
        return ()
    if resolved < MIN_SAMPLES_FOR_FIT:
        return (
            f"The fund has {resolved} resolved trades — too few to generalise "
            f"from, so no post-mortem lessons are being applied. Reason from the "
            f"evidence in front of you.",
        )
    try:
        rows = memory.recent_lessons("*", limit=limit)
    except Exception:
        return ()
    out = []
    for r in rows:
        ctx = r.get("context") or {}
        if not isinstance(ctx, dict) or not ctx.get("code"):
            continue        # only post-mortem lessons; skip operational notes
        out.append(str(r.get("lesson", "")).strip())
    return tuple(out)
