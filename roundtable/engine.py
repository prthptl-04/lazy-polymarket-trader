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
    SEATS_BY_ID,
    eligible_seats,
    excluded_seats,
    voting_seats,
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


def _seat_votes(seat_id: str) -> bool:
    """Unknown seats vote. A seat missing from the registry is more likely a
    test double than an advisory role, and silencing it would hide it."""
    seat = SEATS_BY_ID.get(seat_id)
    return True if seat is None else seat.votes

# Measured against the live API on a real candidate: completing seats spent
# 656-964 output tokens, and the two most verbose — Risk and the Devil's
# Advocate — hit the old 1024 ceiling and were cut off mid-JSON. The Chair,
# which restates six positions plus a transcript, hit the old 2048.
#
# A truncated response is not a degraded answer, it is a silent abstention: the
# seat vanishes from the table and the committee reports a neutral consensus
# that looks considered. So these are sized with real headroom rather than to
# the observed peak. Raising a ceiling costs nothing when it is not reached —
# the model stops at end_turn — so the only calls that get more expensive are
# the ones that were previously broken.
DEFAULT_MAX_TOKENS = 4096
# Down from 8192. The chair no longer reproduces the seat positions — that
# field was 76% of its output — so what remains is a summary and a dissent.
CHAIR_MAX_TOKENS = 3072


@dataclass
class RoundTable:
    """Runs deliberations. `client` is the Anthropic client; inject a fake in tests."""

    client: Any
    router: Any = None          # cache.llm_router.LlmRouter
    memory: Any = None                       # MemoryStore; optional
    model: Optional[str] = None
    max_tokens: int = DEFAULT_MAX_TOKENS
    seat_timeout_seconds: float = 60.0
    # seat_id -> weight from its own record. Refreshed each cycle by the fund;
    # an absent seat counts as 1.0, because an unknown seat is not a
    # discredited one.
    seat_weights: dict = field(default_factory=dict)
    on_debate_start: Any = None              # callback(symbol) when a sitting opens
    on_opinion: Any = None                   # callback(SeatOpinion) for live UI
    on_thesis: Any = None                    # callback(Thesis) when complete
    _last_provider: Optional[str] = None

    # ---------- public API ----------

    async def deliberate(self, candidate: Candidate) -> Thesis:
        # Carrying the candidate is what makes the persisted deliberation a
        # self-contained artifact: the verdict AND what produced it.
        thesis = Thesis(symbol=candidate.symbol, asset_class=candidate.asset_class,
                        candidate=candidate)
        self._persist(thesis, "in_progress")
        # Announce the sitting so a watcher can clear the previous debate
        # before the first seat answers, rather than showing the last symbol's
        # argument under this symbol's name.
        if self.on_debate_start is not None:
            try:
                self.on_debate_start(candidate.symbol)
            except Exception:
                logger.exception("on_debate_start callback failed")

        evidence = candidate.evidence_block()

        # Round 1 — independent, concurrent, and only the seats that can
        # actually hold a view on this asset class. A seat outside its mandate
        # used to answer "neutral" and have that COUNTED, which is how twelve
        # crypto debates produced twelve stand-asides and no trades.
        seats_one = eligible_seats(candidate.asset_class, ROUND_ONE_SEATS)
        round_one = await asyncio.gather(
            *(self._ask_seat(seat, evidence) for seat in seats_one),
            return_exceptions=False,
        )
        thesis.opinions.extend(round_one)
        for opinion in round_one:
            self._emit(opinion)
        self._persist(thesis, "in_progress")

        # Round 2 — rebuttal, with round 1 visible.
        prior = self._render_opinions(round_one)
        for seat in eligible_seats(candidate.asset_class, ROUND_TWO_SEATS):
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
            return self._failed_seat(seat, _parse_failure(raw, self.max_tokens))

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
            f"{self._eligibility_note(candidate.asset_class)}"
            f"{self._independence_note(self._measured_independence())}"
            f"{self._tally_block(thesis.opinions)}"
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
            return self._fallback_consensus(
                thesis, f"chair response {_parse_failure(raw, CHAIR_MAX_TOKENS)}")

        return Consensus(
            signal=_coerce_signal(parsed.get("signal")),
            confidence=_coerce_confidence(parsed.get("confidence")),
            summary=str(parsed.get("summary", "")).strip(),
            # Rendered from what the seats WROTE, never from what the chair
            # retyped. The chair used to be asked for this and spent ~1,450
            # output tokens a deliberation on it — 76% of its output — and a
            # model-regenerated transcript is a paraphrase presented as a
            # record. The fallback path has always derived it this way.
            transcript=self._render_opinions(thesis.opinions),
            dissent=str(parsed.get("dissent", "")).strip(),
            synthesized_by_llm=True,
        )

    @staticmethod
    def _independence_note(measured: Optional[dict]) -> str:
        """Tell the chair how independent the seats measurably are.

        It was guessing, and guessing wrong in a specific direction. From a
        live transcript: "the two bullish seats were shown to be reading the
        same single bar series and the same single-outlet headline feed, so
        their agreement is one framing counted twice rather than corroboration."
        Quant and Sentiment agree at kappa 0.04 — chance. A genuine two-seat
        majority was discarded on a correlation the fund's own measurement
        refutes.

        `roundtable.agreement` has computed this from stored opinions all along
        and nothing consumed it. This is the one place that needed it.

        Supplies the measurement and stops. A line telling the chair what to
        conclude from it would be doing the job the chair exists to do.

        Silent below the sample gate: a chair told "the seats are independent"
        on four debates would discount a real correlation it should have caught.
        """
        from roundtable.agreement import MIN_DELIBERATIONS_FOR_KAPPA
        if not measured:
            return ""
        n = measured.get("n_deliberations") or 0
        kappa = measured.get("mean_kappa")
        if n < MIN_DELIBERATIONS_FOR_KAPPA or kappa is None:
            return ""

        duplicates = measured.get("duplicates") or 0
        if kappa >= 0.7 or duplicates:
            reading = ("the seats are echoing one view rather than reaching it "
                       "separately, so agreement between them is weak evidence")
        elif kappa >= 0.4:
            reading = ("the seats share a good deal of framing, so agreement "
                       "between them is worth less than its count suggests")
        else:
            reading = ("the seats are reaching their calls independently, so "
                       "agreement between them is real corroboration rather "
                       "than one framing counted twice")

        worst = ""
        pairs = [p for p in (measured.get("pairs") or []) if p.get("kappa") is not None]
        if pairs:
            top = max(pairs, key=lambda p: p["kappa"])
            if top["kappa"] >= 0.7:
                worst = (f" The most correlated pair is {top['a']} and {top['b']} "
                         f"at {top['kappa']:.2f}.")

        return (
            f"--- SEAT INDEPENDENCE (measured over {n} past deliberations) ---\n"
            f"Mean pairwise Cohen's kappa between seats is {kappa:.2f} "
            f"({duplicates} pair(s) behaving as one seat): {reading}.{worst}\n"
            f"This is a measurement of the committee's history, not of this "
            f"debate. Weigh it against what the seats actually wrote.\n\n"
        )

    @staticmethod
    def _eligibility_note(asset_class: str) -> str:
        """Tell the chair how big the table actually is.

        It was counting seats that never sat. "One of three eligible seats is
        bullish" and "one of seven seats is bullish" describe the same vote and
        imply opposite conclusions, and the chair reached the second one twelve
        times in a row.

        Silent when every seat is eligible: on equities there is nothing to
        explain, and a line reading "7 of 7" is noise in a prompt that is paid
        for by the token.
        """
        missing = excluded_seats(asset_class)
        if not missing:
            return ""
        total = len(eligible_seats(asset_class)) + len(missing)
        names = ", ".join(s.name for s in missing)
        return (
            f"--- TABLE SIZE ---\n"
            f"{total - len(missing)} of {total} seats were eligible for this "
            f"asset class. NOT CONSULTED: {names} — these seats have no mandate "
            f"on {asset_class} and were never asked, so their silence is not a "
            f"vote to stand aside. Judge the balance of opinion against the "
            f"seats that actually sat, not against the full committee.\n\n"
        )

    def _measured_independence(self) -> Optional[dict]:
        """Pairwise kappa over stored opinions. Never raises — a chair that
        loses this line still works; a chair that loses the debate does not."""
        if self.memory is None:
            return None
        try:
            from roundtable.agreement import pairwise_agreement
            return pairwise_agreement(self.memory.recent_deliberations(limit=200))
        except Exception:
            logger.exception("could not measure seat independence")
            return None

    def _fallback_consensus(self, thesis: Thesis, why: str) -> Consensus:
        """Deterministic vote count when the chair is unavailable.

        Confidence is deliberately capped low: a tally is not a synthesis, and
        the pipeline downstream should treat it as a weak signal rather than
        inheriting whatever conviction the seats happened to express.
        """
        # Weighted, because the fallback is a DECISION and the raw count is a
        # transcript. Two seats the record has discredited must not outvote one
        # that has earned its place.
        tally = thesis.weighted_tally(self.seat_weights)
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
                f"Fallback tally ({why}): {bullish:.1f} bullish / {bearish:.1f} "
                f"bearish / {tally['neutral']:.1f} neutral (weighted by each "
                f"seat's record) across {len(live)} responding seats. "
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

    def _tally_block(self, opinions: Sequence[SeatOpinion]) -> str:
        """The balance of opinion as a count, not as five paragraphs to read.

        The chair was inferring the balance from prose and getting one fact
        wrong repeatedly: it could not distinguish OPPOSITION from ABSENCE OF A
        VIEW. Across 47 of 51 neutral verdicts the typical table was one bullish
        seat, nobody bearish, and the rest with no view — which the chair read
        as a committee-wide stand-aside. `2 bullish / 0 bearish / 3 no view` and
        `2 bullish / 3 bearish` are the same headcount and opposite situations.

        Supplies the arithmetic and stops. The review this came from proposes a
        meta-classifier that OVERTURNS the majority; that would be an
        unfalsifiable override bolted onto the one part of this system that is
        measured. The chair still decides.
        """
        # Advisory seats speak but do not vote. Counting a seat that has never
        # held a direction inflates the "no view" pile the chair reads.
        live = [o for o in opinions
                if not o.failed and _seat_votes(o.seat_id)]
        if not live and not opinions:
            return ""
        bulls = sum(1 for o in live if o.signal == "bullish")
        bears = sum(1 for o in live if o.signal == "bearish")
        flat = sum(1 for o in live if o.signal == "neutral")
        abstained = sum(1 for o in opinions if o.failed)

        parts = [f"{bulls} bullish / {bears} bearish / {flat} no view"]
        if abstained:
            parts.append(f"{abstained} abstained (seat errored, did not weigh in)")
        line = " · ".join(parts)

        notes: list[str] = []
        if (bulls or bears) and not (bulls and bears):
            side = "bullish" if bulls else "bearish"
            notes.append(
                f"NO SEAT ARGUED THE OTHER SIDE. The {side} case is unopposed: "
                f"the remaining seats reported no view, which is not the same as "
                f"disagreeing with it.")
        if len(live) >= 2 and (bulls == len(live) or bears == len(live)):
            notes.append(
                "Every seat that spoke agrees. Unanimity in this panel is a "
                "caution rather than a confirmation — it usually means one "
                "framing was shared rather than independently reached.")

        weighted = self._weighted_line(live)
        return ("--- BALANCE OF OPINION ---\n" + line
                + ("\n" + weighted if weighted else "")
                + ("\n" + " ".join(notes) if notes else "") + "\n\n")

    def _weighted_line(self, live: Sequence[SeatOpinion]) -> str:
        """The same vote, scaled by what each seat's record has earned.

        Closes a gap open since seat weighting was built: `seat_weights` grades
        every seat on its Brier score and `weighted_tally` applied the result in
        exactly one place — `_fallback_consensus`, which runs only after the
        chair has already failed. On the normal path the weight reached the
        chair as a sentence beside each opinion and the model was left to do the
        arithmetic in prose.

        Shown BESIDE the raw count, never instead of it. The raw count is the
        transcript of who said what, and a transcript that silently re-weights
        itself is not one. The weighted line is the decision-relevant number.

        Suppressed while every seat sits at 1.00x — the state with nothing
        resolved — because a weighted line identical to the raw one is noise in
        a prompt paid for by the token, and it would imply an adaptation that
        has not happened.

        An unknown seat counts as 1.0: untested is not discredited, the same
        stance `seat_weights` takes.
        """
        weights = self.seat_weights or {}
        if not any(abs(float(w) - 1.0) > 0.01 for w in weights.values()):
            return ""

        counts = {"bullish": 0.0, "bearish": 0.0, "neutral": 0.0}
        for o in live:
            counts[o.signal] = counts.get(o.signal, 0.0) + float(
                weights.get(o.seat_id, 1.0))

        moved = [
            f"{o.seat_name} {float(weights[o.seat_id]):.2f}x"
            for o in live
            if o.seat_id in weights and abs(float(weights[o.seat_id]) - 1.0) > 0.01
        ]
        detail = (" Adjusted by record: " + ", ".join(moved) + "." if moved else "")
        return (f"Weighted by each seat's measured record: "
                f"{counts['bullish']:.2f} bullish / {counts['bearish']:.2f} bearish "
                f"/ {counts['neutral']:.2f} no view.{detail}")

    def _render_opinions(self, opinions: Sequence[SeatOpinion]) -> str:
        """The seat positions, each annotated with that seat's own record.

        The Chair is TOLD which colleagues have been reliable rather than
        having their votes silently re-weighted behind it. Telling the seat
        that synthesises is strictly more information than adjusting the
        arithmetic afterwards — and it keeps the transcript honest, because a
        reader can see both the vote and the reason it was discounted.
        """
        blocks = []
        for o in opinions:
            if o.failed:
                blocks.append(f"[{o.seat_name}] ABSTAINED ({o.error})")
                continue
            weight = self.seat_weights.get(o.seat_id)
            record = ""
            if weight is not None and abs(weight - 1.0) > 0.01:
                record = (f" [track record: this seat's calls have been "
                          f"{'better' if weight > 1 else 'worse'} than average; "
                          f"weight {weight:.2f}]")
            parts = [
                f"[{o.seat_name}] {o.signal.upper()} "
                f"(confidence {o.confidence:.0f}){record}",
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


def _looks_truncated(raw: str) -> bool:
    """Did this response start a JSON object and never finish it?

    Worth separating from "unparseable", because the two have different fixes
    and the wrong label sends the next investigation somewhere useless. A
    refusal or a prose answer means the prompt is wrong; an unterminated object
    means the token budget is. The previous single label cost an API probe to
    tell apart.
    """
    if not raw:
        return False
    text = raw.strip()
    if "```" in text:
        for chunk in text.split("```"):
            chunk = chunk.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("{"):
                text = chunk
                break
    start = text.find("{")
    if start == -1:
        return False
    # Balanced braces would have parsed; an excess of opens means it was cut.
    depth = 0
    in_string = escaped = False
    for ch in text[start:]:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth > 0 or in_string


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


def _parse_failure(raw: str, budget: int) -> str:
    """Why the response could not be read, in terms that name the fix."""
    if _looks_truncated(raw):
        logger.warning(
            "response truncated at max_tokens=%s (%d chars); raise the budget",
            budget, len(raw or ""),
        )
        return f"response truncated at max_tokens={budget} — the budget is too small"
    return "unparseable response"


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
