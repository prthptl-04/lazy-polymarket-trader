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
# ...and below this on a material WIN. Confidence drives size, so a 2R win taken
# at 52% conviction was under-sized by the very mechanism that over-sizes a
# confident loss. One fault, two signs — recording only the loss side is what
# gives a post-mortem corpus its pessimistic tilt.
UNDERCONFIDENCE_THRESHOLD = 60.0
# Lessons older than this stop being shown; markets change and a stale rule is
# worse than none.
MAX_LESSONS_SHOWN = 6


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str
    severity: str = "note"          # note | warning


# A loss worth learning from, as a fraction of the risk the position was sized
# for. A quarter of planned risk is the smallest move that says something about
# the thesis rather than about the spread.
MATERIAL_LOSS_R = 0.25

# Used only when no stop was recorded and the loss cannot be expressed in R.
MIN_MATERIAL_LOSS_PCT = 0.02


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
            # A win is still not automatically right, and the bar does not move:
            # a finding is recorded only where arithmetic establishes one. What
            # a win CAN establish is a calibration fault, which is the same
            # thing the loss path already records at the other sign — see
            # `_underconfidence`. Everything else a win might "teach" is
            # survivorship, and claiming it teaches superstition.
            if _is_material(realized_return, plan):
                finding = self._underconfidence(symbol, realized_return,
                                                (thesis or {}).get("payload") or thesis or {})
                if finding is not None:
                    findings.append(finding)
            return findings

        # And nothing establishable from a loss too small to be a signal.
        # Every finding here becomes a lesson under agent_id="*", which
        # `recent_lesson_lines` injects into every subsequent deliberation — so
        # a trivial loss does not merely fail to teach, it actively teaches the
        # whole committee an overconfidence penalty derived from noise.
        #
        # Judged in R, against the risk the position was SIZED for, because a
        # volatile name with a wide stop must not be measured by a tight name's
        # yardstick: the same 1% loss is a stop-out for one and a rounding
        # error for the other.
        if not _is_material(realized_return, plan):
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
    def _underconfidence(symbol: str, realized_return: float,
                         payload: dict) -> Optional[Finding]:
        """A call that worked and was barely backed.

        Deliberately narrow. It fires on the CONSENSUS confidence, not on any
        one seat, and only on a directional call — a neutral committee that
        happened to be carried into a winner has no conviction to have
        understated.
        """
        consensus = payload.get("consensus") or {}
        confidence = consensus.get("confidence")
        signal = consensus.get("signal")
        if signal in (None, "neutral") or not confidence:
            return None
        if confidence > UNDERCONFIDENCE_THRESHOLD:
            return None
        return Finding(
            "underconfident_win",
            f"{symbol}: the committee was only {confidence:.0f}% confident on a "
            f"{signal} call that returned {realized_return * 100:+.1f}%. Confidence "
            "sets the size, so conviction stated below what the evidence "
            "supported was paid for in a position smaller than the call deserved.",
        )

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

    def record(self, findings: list[Finding], *, symbol: str,
               asset_class: Optional[str] = None) -> int:
        """Persist findings as lessons every seat will read. Returns the count."""
        if not findings or self.memory is None:
            return 0
        written = 0
        for f in findings:
            try:
                self.memory.record_lesson(
                    "*", f.detail, context={"code": f.code, "symbol": symbol,
                                            "asset_class": asset_class,
                                            "severity": f.severity},
                )
                written += 1
            except Exception:
                logger.exception("could not record lesson %s", f.code)
        return written

    def run(self, *, symbol: str, realized_return: float,
            thesis: Optional[dict], exit_reason: str = "stop",
            plan: Optional[dict] = None,
            asset_class: Optional[str] = None) -> list[Finding]:
        findings = self.analyse(symbol=symbol, realized_return=realized_return,
                                thesis=thesis, exit_reason=exit_reason, plan=plan)
        self.record(findings, symbol=symbol, asset_class=asset_class)
        return findings


def _is_material(realized_return: float, plan: Optional[dict]) -> bool:
    """Is this loss big enough, relative to its own plan, to be evidence?"""
    loss = abs(realized_return)
    entry = (plan or {}).get("entry")
    stop = (plan or {}).get("stop")
    try:
        planned_risk = abs(float(entry) - float(stop)) / float(entry)
    except (TypeError, ValueError, ZeroDivisionError):
        planned_risk = None
    if not planned_risk:
        # No stop recorded, so the loss cannot be expressed in R. An absolute
        # floor is a worse instrument than R but a far better one than zero.
        return loss >= MIN_MATERIAL_LOSS_PCT
    return loss >= MATERIAL_LOSS_R * planned_risk


def recent_lesson_lines(memory: Any, limit: int = MAX_LESSONS_SHOWN) -> tuple[str, ...]:
    """Every post-mortem lesson, newest first. Unscoped — prefer
    `relevant_lesson_lines`, which is this with a scope applied."""
    return relevant_lesson_lines(memory, limit=limit)


def relevant_lesson_lines(
    memory: Any,
    *,
    asset_class: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = MAX_LESSONS_SHOWN,
) -> tuple[str, ...]:
    """Lessons rendered for the evidence block of ONE deliberation.

    Goes to the seats as *evidence*, not as a system-prompt edit — the system
    blocks are cache-tagged, so mutating them would discard the prompt cache
    every time the fund learns something.

    RETRIEVED BY RELEVANCE, not by recency. This used to hand every debate the
    newest six lessons whatever they were about, so a finding about an equity
    gapping through its stop overnight was being read as evidence by a weekend
    BTC deliberation. Crypto does not gap overnight; it has no overnight. No
    hit-rate study is needed to call that a defect, which is why it is fixed
    ahead of any measurement of whether the lessons help at all.

    Ranking, highest first:
      3  this instrument, in this asset class
      2  this instrument (recorded before scoping existed)
      1  this asset class
      0  unscoped legacy
      -  a DIFFERENT asset class is dropped, not down-ranked

    Recency breaks ties, so within one relevance band the ordering is what it
    always was. Unscoped history is kept rather than deleted: it is what the
    fund has learned, and ranking it last fixes the leak without discarding it.

    STILL GATED ON EVIDENCE, ahead of relevance. Recording is unchanged —
    findings are written, audited and shown to a human on the lessons panel.
    What is gated is the INJECTION into future deliberations, because the
    asymmetry is brutal: a lesson that is noise persists and compounds across
    every subsequent debate, while a lesson withheld costs one cycle of
    un-learned insight. A well-targeted lesson drawn from four trades is still
    drawn from four trades.

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
        # Over-fetch: the scope filter runs after the query, so asking for
        # `limit` rows would return fewer than `limit` relevant ones.
        rows = memory.recent_lessons("*", limit=limit * 8)
    except Exception:
        return ()

    scored: list[tuple[int, str]] = []
    for r in rows:
        ctx = r.get("context") or {}
        if not isinstance(ctx, dict) or not ctx.get("code"):
            continue        # only post-mortem lessons; skip operational notes
        text = str(r.get("lesson", "")).strip()
        if not text:
            continue
        lesson_class = ctx.get("asset_class")
        if asset_class and lesson_class and lesson_class != asset_class:
            continue        # a different market; not evidence here
        score = 0
        if symbol and ctx.get("symbol") == symbol:
            score += 2
        if asset_class and lesson_class == asset_class:
            score += 1
        scored.append((score, text))

    # `sorted` is stable, so recency (the query order) survives within a band.
    return tuple(text for _, text in
                 sorted(scored, key=lambda pair: -pair[0])[:limit])
