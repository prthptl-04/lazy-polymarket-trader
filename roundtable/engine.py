"""Round-table deliberation engine.

Three stages per candidate:

  1. Round 1 — Analyst, Sentiment, Quant, Risk, Corroborator run **in parallel**, each
     only the candidate. Parallel is not just for speed: it is what makes their
     independence real, since no seat can anchor on another's conclusion.
  2. Round 2 — the Devil's Advocate sees round 1 and must attack the majority.
  3. Chair — one synthesis call produces the transcript and the consensus.

Seven LLM calls per candidate. The alternative designs and why not:
- One call role-playing all six seats is ~6x cheaper and produces a convincing
  transcript with zero independent judgement — every voice carries one model's
  biases, so the dissent is decorative.
- Every seat speaking in every round is richer and ~3x the cost, and most of
  the extra turns restate round 1.

What this engine does NOT do
----------------------------
It does not decide whether a trade happens. It produces a `Thesis`. Turning
that into an order still passes `OutcomeGrader.evaluate` and then every gate in
`VenueRouter` — session, PDT, kill-switch, spread. A unanimous bullish table
with a missing stop gets refused by the Risk Manager seat, and if it somehow
got past that it would be refused by the grader. Consensus is an input to the
pipeline, never an override of it.

Async placement (CLAUDE.md #16): all network I/O, off the hot path entirely.
The trading loop never waits on a deliberation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from cache.prompt_cache import cached_create
from roundtable.seats import (
    CHAIR_SYSTEM_PROMPT,
    ROUND_ONE_SEATS,
    ROUND_TWO_SEATS,
    Seat,
)
from roundtable.types import (
    VALID_SIGNALS,
    Candidate,
    Consensus,
    SeatOpinion,
    Signal,
    Thesis,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 1024
CHAIR_MAX_TOKENS = 2048


@dataclass
class RoundTable:
    """Runs deliberations. `client` is the Anthropic client; inject a fake in tests."""

    client: Any
    router: Any = None          # cache.llm_router.LlmRouter
    memory: Any = None                       # MemoryStore; optional
    model: Optional[str] = None
    max_tokens: int = DEFAULT_MAX_TOKENS
    seat_timeout_seconds: float = 60.0
    on_opinion: Any = None                   # callback(SeatOpinion) for live UI
    on_thesis: Any = None                    # callback(Thesis) when complete
    _last_provider: Optional[str] = None

    # ---------- public API ----------

    async def deliberate(self, candidate: Candidate) -> Thesis:
        thesis = Thesis(symbol=candidate.symbol, asset_class=candidate.asset_class)
        self._persist(thesis, "in_progress")

        evidence = candidate.evidence_block()

        # Round 1 — independent, concurrent.
        round_one = await asyncio.gather(
            *(self._ask_seat(seat, evidence) for seat in ROUND_ONE_SEATS),
            return_exceptions=False,
        )
        thesis.opinions.extend(round_one)
        for opinion in round_one:
            self._emit(opinion)
        self._persist(thesis, "in_progress")

        # Round 2 — rebuttal, with round 1 visible.
        prior = self._render_opinions(round_one)
        for seat in ROUND_TWO_SEATS:
            opinion = await self._ask_seat(
                seat, f"{evidence}\n\n--- THE OTHER SEATS ---\n{prior}"
            )
            thesis.opinions.append(opinion)
            self._emit(opinion)
        self._persist(thesis, "in_progress")

        # Chair — synthesis.
        thesis.consensus = await self._synthesize(candidate, thesis)
        thesis.status = "complete"
        self._persist(thesis, "complete")

        if self.on_thesis is not None:
            try:
                self.on_thesis(thesis)
            except Exception:       # a UI callback must not kill a deliberation
                logger.exception("on_thesis callback failed")
        return thesis

    # ---------- seats ----------

    async def _ask_seat(self, seat: Seat, user_content: str) -> SeatOpinion:
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self._call,
                    seat.system_prompt,
                    user_content,
                    self.max_tokens,
                ),
                timeout=self.seat_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return self._failed_seat(seat, "timed out")
        except Exception as e:
            return self._failed_seat(seat, f"{type(e).__name__}: {e}")

        parsed = _parse_json(raw)
        if parsed is None:
            return self._failed_seat(seat, "unparseable response")

        return SeatOpinion(
            seat_id=seat.id,
            seat_name=seat.name,
            signal=_coerce_signal(parsed.get("signal")),
            confidence=_coerce_confidence(parsed.get("confidence")),
            reasoning=str(parsed.get("reasoning", "")).strip(),
            key_points=tuple(_as_str_list(parsed.get("key_points"))),
            concerns=tuple(_as_str_list(parsed.get("concerns"))),
        )

    @staticmethod
    def _failed_seat(seat: Seat, error: str) -> SeatOpinion:
        """A dead seat abstains. It must never be silently counted as agreement."""
        return SeatOpinion(
            seat_id=seat.id, seat_name=seat.name,
            signal="neutral", confidence=0.0,
            reasoning=f"seat unavailable: {error}",
            failed=True, error=error,
        )

    # ---------- chair ----------

    async def _synthesize(self, candidate: Candidate, thesis: Thesis) -> Consensus:
        live = [o for o in thesis.opinions if not o.failed]
        if not live:
            return self._fallback_consensus(thesis, "every seat failed; no basis for a view")

        content = (
            f"{candidate.evidence_block()}\n\n"
            f"--- SEAT POSITIONS ---\n{self._render_opinions(thesis.opinions)}"
        )
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    self._call, CHAIR_SYSTEM_PROMPT, content, CHAIR_MAX_TOKENS
                ),
                timeout=self.seat_timeout_seconds,
            )
        except Exception as e:
            return self._fallback_consensus(thesis, f"chair call failed: {type(e).__name__}")

        parsed = _parse_json(raw)
        if parsed is None:
            return self._fallback_consensus(thesis, "chair response unparseable")

        return Consensus(
            signal=_coerce_signal(parsed.get("signal")),
            confidence=_coerce_confidence(parsed.get("confidence")),
            summary=str(parsed.get("summary", "")).strip(),
            transcript=str(parsed.get("transcript", "")).strip(),
            dissent=str(parsed.get("dissent", "")).strip(),
            synthesized_by_llm=True,
        )

    def _fallback_consensus(self, thesis: Thesis, why: str) -> Consensus:
        """Deterministic vote count when the chair is unavailable.

        Confidence is deliberately capped low: a tally is not a synthesis, and
        the pipeline downstream should treat it as a weak signal rather than
        inheriting whatever conviction the seats happened to express.
        """
        tally = thesis.tally()
        bullish, bearish = tally["bullish"], tally["bearish"]
        if bullish > bearish:
            signal: Signal = "bullish"
        elif bearish > bullish:
            signal = "bearish"
        else:
            signal = "neutral"

        live = [o for o in thesis.opinions if not o.failed]
        confidence = min(50.0, sum(o.confidence for o in live) / len(live)) if live else 0.0

        return Consensus(
            signal=signal,
            confidence=round(confidence, 1),
            summary=(
                f"Fallback tally ({why}): {bullish} bullish / {bearish} bearish / "
                f"{tally['neutral']} neutral across {len(live)} responding seats. "
                "Confidence capped — this is a vote count, not a synthesis."
            ),
            transcript=self._render_opinions(thesis.opinions),
            dissent="; ".join(
                f"{o.seat_name}: {o.reasoning}" for o in live if o.signal != signal
            ),
            synthesized_by_llm=False,
        )

    # ---------- plumbing ----------

    def _call(self, system: str, content: str, max_tokens: int) -> str:
        """One model call, through the mandated cache wrapper (rule #2).

        With a router attached the provider may be Anthropic or Gemini. Seats
        never learn which: the call is stateless — full system prompt plus the
        whole evidence block every time — so a mid-deliberation switch loses no
        context. The provider is recorded for the transcript, not for the seat.
        """
        kwargs: dict[str, Any] = {
            "system": system,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
        if self.model:
            kwargs["model"] = self.model
        if self.router is not None:
            text, provider = self.router.create(**kwargs)
            self._last_provider = provider
            return text
        response = cached_create(self.client, **kwargs)
        return _extract_text(response)

    @staticmethod
    def _render_opinions(opinions: Sequence[SeatOpinion]) -> str:
        blocks = []
        for o in opinions:
            if o.failed:
                blocks.append(f"[{o.seat_name}] ABSTAINED ({o.error})")
                continue
            parts = [
                f"[{o.seat_name}] {o.signal.upper()} (confidence {o.confidence:.0f})",
                o.reasoning,
            ]
            if o.key_points:
                parts.append("Key points: " + "; ".join(o.key_points))
            if o.concerns:
                parts.append("Concerns: " + "; ".join(o.concerns))
            blocks.append("\n".join(parts))
        return "\n\n".join(blocks)

    def _persist(self, thesis: Thesis, status: str) -> None:
        thesis.status = status
        if self.memory is None:
            return
        try:
            self.memory.save_deliberation(
                thesis_id=thesis.thesis_id,
                symbol=thesis.symbol,
                asset_class=thesis.asset_class,
                status=status,
                payload=thesis.as_payload(),
                signal=thesis.signal,
                confidence=thesis.confidence,
            )
        except Exception:
            # Losing the transcript must not lose the trade decision.
            logger.exception("failed to persist deliberation %s", thesis.thesis_id)

    def _emit(self, opinion: SeatOpinion) -> None:
        if self.on_opinion is None:
            return
        try:
            self.on_opinion(opinion)
        except Exception:
            logger.exception("on_opinion callback failed")


# ---------- parsing helpers ----------
#
# Model output is untrusted input. Every helper returns a safe default rather
# than raising, because one malformed field must not abort a deliberation.

def _extract_text(response: Any) -> str:
    """Pull text out of an Anthropic response, skipping thinking blocks."""
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts = []
    for block in content:
        btype = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if btype and btype != "text":
            continue                     # thinking / tool_use blocks
        text = getattr(block, "text", None) or (
            block.get("text") if isinstance(block, dict) else None
        )
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _parse_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    text = raw.strip()
    # Models wrap JSON in fences despite being told not to.
    if "```" in text:
        fenced = text.split("```")
        for chunk in fenced:
            chunk = chunk.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("{"):
                text = chunk
                break
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_signal(value: Any) -> Signal:
    text = str(value or "").strip().lower()
    return text if text in VALID_SIGNALS else "neutral"


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return []
