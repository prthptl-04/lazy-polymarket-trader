"""Forward Deployment's runtime feedback channel back to the Architect."""

from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass
class FeedbackEvent:
    kind: str           # e.g. "grader_rejected", "cclob_5xx", "wallet_sign_failed"
    detail: dict[str, Any]
    ts: float = field(default_factory=time)


class LiveFeedback:
    """Append-only in-memory ring of recent feedback events.

    The Forward Deployment Agent writes here; the Architect (via the orchestrator)
    reads from here to build its next-iteration plan. Persistence is intentionally
    light — recurring patterns are promoted to roadmap items by the Product Agent.
    """

    def __init__(self, capacity: int = 256) -> None:
        self.capacity = capacity
        self._events: list[FeedbackEvent] = []

    def record(self, kind: str, **detail: Any) -> FeedbackEvent:
        event = FeedbackEvent(kind=kind, detail=detail)
        self._events.append(event)
        if len(self._events) > self.capacity:
            self._events = self._events[-self.capacity:]
        return event

    def recent(self, kind: str | None = None, limit: int = 50) -> list[FeedbackEvent]:
        items = [e for e in self._events if kind is None or e.kind == kind]
        return items[-limit:]
