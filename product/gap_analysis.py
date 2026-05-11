"""Detect feature gaps between current bot capabilities and observed Polymarket behavior.

The Product Agent calls this against monitoring/live_feedback events to surface
recurring patterns that belong on the roadmap.
"""

from collections import Counter
from dataclasses import dataclass

from monitoring.live_feedback import FeedbackEvent


@dataclass(frozen=True)
class GapTicket:
    kind: str          # the recurring feedback kind
    count: int
    suggested_owner: str   # "architect" or "forward_deployment"
    note: str


_OWNER_BY_KIND = {
    "grader_rejected": "architect",
    "clob_5xx": "architect",
    "clob_timeout": "architect",
    "wallet_sign_failed": "forward_deployment",
    "depth_below_threshold": "architect",
}


def detect_gaps(events: list[FeedbackEvent], threshold: int = 3) -> list[GapTicket]:
    """Return one ticket per feedback kind that occurred >= threshold times."""
    counts = Counter(e.kind for e in events)
    tickets: list[GapTicket] = []
    for kind, count in counts.items():
        if count < threshold:
            continue
        tickets.append(
            GapTicket(
                kind=kind,
                count=count,
                suggested_owner=_OWNER_BY_KIND.get(kind, "architect"),
                note=f"{kind} recurred {count}x — escalate via orchestrator.",
            )
        )
    return tickets
