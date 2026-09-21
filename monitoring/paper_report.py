"""What paper trading is telling us, and what to change because of it.

Two jobs, and the second is the reason this exists.

**Report state.** Progress toward the two thresholds that gate everything, and
which mechanisms are still dormant because of them.

**Pre-register the triggers.** Each one names what to watch, the threshold that
counts, and the change it implies — written down BEFORE the results arrive and
then evaluated against live state. A fund that decides after the fact what
counts as evidence will always find evidence for what it already wanted, and a
committee of LLMs is an unusually fluent machine for producing that evidence.

The report is deliberately pessimistic about an empty fund. On day zero almost
every trigger is un-evaluable and says so. A report that rendered green because
nothing had failed yet would state the opposite of the truth.

    python -m monitoring.paper_report            # markdown to stdout
    python -m monitoring.paper_report --json
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Rule #13: 50 graded paper trades before a live flip is even considerable.
LIVE_GATE_TRADES = 50
# roundtable.calibration: below this nothing is fitted, weighted or injected.
LEARNING_SAMPLES = 30
# The fund's fixed 2xATR stop / 3xATR target gives b = 1.5, so break-even is
# 1/(1+b) = 40%. Not 50% — a trigger set at 50% would fire on a fund that is
# making money, which is how a working strategy gets switched off.
BREAKEVEN_HIT_RATE = 0.40


@dataclass(frozen=True)
class Trigger:
    """A pre-registered "if this, change that".

    `evaluate` returns True (fired), False (ok), or None (not yet evaluable).
    None is the honest answer far more often than either of the others.
    """

    id: str
    watch: str
    threshold: str
    action: str
    evaluate: Callable[[dict], Optional[bool]]
    detail: Callable[[dict], str] = lambda _: ""


def _hit_rate(d: dict) -> Optional[float]:
    card = d.get("scorecard") or {}
    committee = card.get("committee") or {}
    return committee.get("hit_rate") if card.get("resolved", 0) >= LEARNING_SAMPLES else None


TRIGGERS: tuple[Trigger, ...] = (
    Trigger(
        id="no_candidates_debated",
        watch="cycles run vs deliberations opened",
        threshold="20+ cycles and still zero deliberations",
        action="The watchlist or the pre-screen is the problem, not the market. "
               "Check `config/fund.toml` watchlists are populated and read "
               "`prescreened_out` reasons in the cycle report — 'no exit plan' "
               "means bars or ATR are missing, not that nothing was tradable.",
        evaluate=lambda d: (d["cycles"] >= 20 and d["deliberations"] == 0)
                           if d["cycles"] >= 20 else None,
        detail=lambda d: f"{d['cycles']} cycles, {d['deliberations']} deliberations",
    ),
    Trigger(
        id="debates_never_become_trades",
        watch="deliberations vs orders submitted",
        threshold="15+ deliberations and fewer than 10% become orders",
        action="The committee is reasoning but the pipeline is refusing. Read the "
               "refusal reasons: a grader veto is a different fix from a router "
               "gate, and a confidence shrink that is too pessimistic is a third. "
               "Do NOT loosen the grader to raise the count — that is how a "
               "strategy is talked into paying for its own activity.",
        evaluate=lambda d: (d["submitted"] / d["deliberations"] < 0.10)
                           if d["deliberations"] >= 15 else None,
        detail=lambda d: f"{d['deliberations']} debates, {d['submitted']} orders",
    ),
    Trigger(
        id="edge_below_breakeven",
        watch="committee hit rate against the 1.5R break-even",
        threshold=f"{LEARNING_SAMPLES}+ resolved and hit rate below "
                  f"{BREAKEVEN_HIT_RATE:.0%}",
        action="The edge is not there at this geometry. Before changing the "
               "strategy, check the replay: `python -m roundtable.replay` says "
               "whether a weighted vote would have decided better than the chair. "
               "If it would not, the problem is the evidence or the seats, not "
               "the aggregation.",
        evaluate=lambda d: (hr < BREAKEVEN_HIT_RATE)
                           if (hr := _hit_rate(d)) is not None else None,
        detail=lambda d: (f"hit rate {hr:.1%} vs {BREAKEVEN_HIT_RATE:.0%} needed"
                          if (hr := _hit_rate(d)) is not None
                          else f"{d['resolved']}/{LEARNING_SAMPLES} resolved"),
    ),
    Trigger(
        id="committee_costs_more_than_it_makes",
        watch="LLM spend against realised P&L",
        threshold="10+ closed trades and token spend exceeds realised profit",
        action="A fund that spends more on thinking than it makes from trading is "
               "losing money in a novel way. Lower `max_candidates_per_cycle`, or "
               "raise the pre-screen bar so fewer names reach eight LLM calls. "
               "The screens cost microseconds; the table costs dollars.",
        evaluate=lambda d: (d["spend_usd"] > max(0.0, d["realized_usd"]))
                           if d["closed"] >= 10 else None,
        detail=lambda d: f"${d['spend_usd']:.2f} spent, ${d['realized_usd']:+.2f} realised",
    ),
    Trigger(
        id="stops_dominate_targets",
        watch="exits by reason",
        threshold="10+ closed and stops outnumber targets more than 3:1",
        action="The 2xATR/3xATR geometry is wrong for this universe — the stop is "
               "inside the noise. Widen the stop multiplier in `finance/exits.py` "
               "OR shorten the target, but re-derive b and the break-even hit "
               "rate when you do: they move together, and changing one alone "
               "silently changes what 'working' means.",
        evaluate=lambda d: (d["stops"] > d["targets"] * 3)
                           if d["closed"] >= 10 else None,
        detail=lambda d: f"{d['stops']} stops, {d['targets']} targets",
    ),
    Trigger(
        id="a_seat_is_worse_than_a_coin",
        watch="per-seat Brier once scored",
        threshold=f"a seat with {LEARNING_SAMPLES}+ calls and Brier above 0.25",
        action="Its vote weight drops automatically — no action needed first. If "
               "it stays bad across another 30 calls, the mandate is wrong, not "
               "the weighting: rewrite the seat's prompt or retire the seat. Do "
               "not delete a dissenting seat for dissenting.",
        evaluate=lambda d: bool(d["bad_seats"]) if d["seats_scored"] else None,
        detail=lambda d: (", ".join(d["bad_seats"]) or "all scored seats beat a coin")
                         if d["seats_scored"] else f"0 of 7 seats scored",
    ),
    Trigger(
        id="evidence_sources_keep_degrading",
        watch="the PROVENANCE block and `degraded` on the catalyst feed",
        threshold="a source stale or unavailable in most deliberations",
        action="Fix the integration rather than letting the seats discount it "
               "forever. A source that is permanently STALE trains the committee "
               "to ignore staleness warnings, which is worse than not having the "
               "source at all.",
        evaluate=lambda _: None,     # needs per-deliberation history to judge
        detail=lambda _: "read the Provenance panel during live cycles",
    ),
    Trigger(
        id="weekend_crypto_never_fills",
        watch="crypto orders resting vs filled at weekends",
        threshold="3+ weekends with orders placed and nothing filled",
        action="The resting-at-mark model is not being reached. Either the limit "
               "is set at a mark the market never revisits, or `match_resting` is "
               "not being called with fresh quotes. Do NOT switch to crossing the "
               "spread — that is the 187bps cost that made weekend crypto "
               "unviable in the first place.",
        evaluate=lambda _: None,     # needs weekend-scoped fill history
        detail=lambda _: "no weekend cycles recorded yet",
    ),
)


def build_report(runtime: Any) -> dict:
    """Assemble live state and evaluate every trigger against it."""
    facts = _facts(runtime)
    triggers = []
    for t in TRIGGERS:
        try:
            verdict = t.evaluate(facts)
        except Exception:
            logger.exception("trigger %s failed to evaluate", t.id)
            verdict = None
        triggers.append({
            "id": t.id, "watch": t.watch, "threshold": t.threshold,
            "action": t.action,
            "state": "fired" if verdict else "ok" if verdict is False else "not_evaluable",
            "detail": _safe(t.detail, facts),
        })

    graded = facts["graded"]
    resolved = facts["resolved"]
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resolved": resolved,
        "gates": {
            "live_trading": {"have": graded, "required": LIVE_GATE_TRADES,
                             "unlocks": "rule #13 live-flip eligibility"},
            "learning": {"have": resolved, "required": LEARNING_SAMPLES,
                         "unlocks": "seat weights, confidence shrink, lesson injection"},
        },
        "dormant": _dormant(facts),
        "triggers": triggers,
        "facts": facts,
    }


def _dormant(f: dict) -> list[dict]:
    """Mechanisms that exist, are wired, and are deliberately not yet acting."""
    short = max(0, LEARNING_SAMPLES - f["resolved"])
    return [
        {"name": "seat vote weights",
         "state": f"all 7 seats at 1.00x ({f['seats_scored']} scored)",
         "waiting_on": f"{short} more resolved theses",
         "why": "down-weighting on a handful of calls converges the committee on "
                "whoever was lucky first"},
        {"name": "confidence shrink",
         "state": "pessimistic constant 0.50, not fitted",
         "waiting_on": f"{short} more resolved theses",
         "why": "LLM confidence is uncalibrated until measured against outcomes"},
        {"name": "post-mortem lessons",
         "state": f"{f['lessons']} recorded, none injected",
         "waiting_on": f"{short} more resolved theses",
         "why": "a lesson that is noise compounds across every later debate"},
    ]


def _facts(rt: Any) -> dict:
    def call(name, default):
        try:
            return getattr(rt, name)()
        except Exception:
            logger.exception("could not read %s", name)
            return default

    progress = call("paper_progress", {})
    record = call("record", {})
    costs = call("costs", {})
    card = call("scorecard", {})
    status = call("status", {})
    closed = call("_closed_trades", [])

    reasons = [str(t.get("reason") or "").lower() for t in closed]
    seats = [s for s in (card.get("seats") or []) if s.get("scored")]
    return {
        "graded": progress.get("graded_paper_trades", 0),
        "resolved": card.get("resolved", 0),
        "deliberations": progress.get("deliberations", 0),
        "cycles": ((status.get("metrics") or {}).get("cycles", 0)),
        "submitted": ((status.get("metrics") or {}).get("submitted", 0)),
        "closed": record.get("closed", 0),
        "realized_usd": record.get("realized_usd", 0.0) or 0.0,
        "spend_usd": costs.get("total_burned_usd", 0.0) or 0.0,
        "lessons": progress.get("lessons_learned", 0),
        "seats_scored": len(seats),
        "bad_seats": [s["seat_name"] for s in seats if not s.get("beats_coin_flip")],
        "stops": sum(1 for r in reasons if "stop" in r),
        "targets": sum(1 for r in reasons if "target" in r),
        "scorecard": card,
    }


def _safe(fn: Callable[[dict], str], facts: dict) -> str:
    try:
        return fn(facts)
    except Exception:
        return ""


# ---------------------------------------------------------------- rendering

_STATE = {"fired": "FIRED", "ok": "ok", "not_evaluable": "not yet evaluable"}


def render_markdown(report: dict) -> str:
    f = report["facts"]
    live, learn = report["gates"]["live_trading"], report["gates"]["learning"]
    fired = [t for t in report["triggers"] if t["state"] == "fired"]

    out = [
        "# Paper-trading report",
        f"_Generated {report['generated']}_",
        "",
        "## Where the fund is",
        "",
        "| | Have | Needed | Unlocks |",
        "|---|---|---|---|",
        f"| Graded paper trades | {live['have']} | {live['required']} | {live['unlocks']} |",
        f"| Resolved theses | {learn['have']} | {learn['required']} | {learn['unlocks']} |",
        "",
        f"Cycles run: {f['cycles']} · deliberations: {f['deliberations']} · "
        f"orders: {f['submitted']} · closed: {f['closed']} · "
        f"realised ${f['realized_usd']:+.2f} · LLM spend ${f['spend_usd']:.2f}",
        "",
        "## Dormant by design",
        "",
    ]
    for d in report["dormant"]:
        out.append(f"- **{d['name']}** — {d['state']}. Waiting on {d['waiting_on']}. "
                   f"_{d['why']}._")

    out += ["", "## Pre-registered triggers", "",
            "Written before the results, so the threshold is a commitment rather "
            "than a metric to be reinterpreted once it is inconvenient.", ""]
    if fired:
        out.append(f"**{len(fired)} fired.** Act on these first.\n")
    for t in report["triggers"]:
        mark = "**FIRED**" if t["state"] == "fired" else _STATE[t["state"]]
        out += [f"### `{t['id']}` — {mark}",
                f"- **Watch:** {t['watch']}",
                f"- **Threshold:** {t['threshold']}",
                f"- **Now:** {t['detail'] or 'no data yet'}",
                f"- **If it fires:** {t['action']}", ""]
    return "\n".join(out)


def _demo() -> None:
    assert len({t.id for t in TRIGGERS}) == len(TRIGGERS)
    assert all(t.watch and t.threshold and len(t.action) > 20 for t in TRIGGERS)
    # An empty fund must evaluate nothing.
    empty = {"cycles": 0, "deliberations": 0, "submitted": 0, "closed": 0,
             "resolved": 0, "realized_usd": 0.0, "spend_usd": 0.0,
             "seats_scored": 0, "bad_seats": [], "stops": 0, "targets": 0,
             "scorecard": {"resolved": 0}}
    assert all(t.evaluate(empty) is None for t in TRIGGERS), "day zero must be silent"
    print("paper_report self-check passed")


if __name__ == "__main__":
    import json
    import sys

    if "--demo" in sys.argv:
        _demo()
        raise SystemExit(0)

    from dashboard.runtime import DashboardRuntime
    from memory.store import MemoryStore

    report = build_report(DashboardRuntime(memory=MemoryStore()))
    print(json.dumps(report, indent=2, default=str) if "--json" in sys.argv
          else render_markdown(report))
