"""WebSocket publish-subscribe hub.

Single producer (the AutonomousLoop's `on_status` callback + decision pushes)
fans out to N browser tabs. Each tab gets its own asyncio.Queue so a slow
client can't backpressure the publisher.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WebSocketHub:
    queues: list[asyncio.Queue] = field(default_factory=list)
    max_queue_size: int = 256

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue_size)
        self.queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self.queues.remove(q)
        except ValueError:
            pass

    def publish(self, message: dict | str) -> None:
        """Fan out without awaiting. Drops the message for any queue that's
        full — backpressure on the producer would punish the trading loop."""
        payload = message if isinstance(message, str) else json.dumps(message, default=str)
        for q in list(self.queues):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop. The next status tick will catch the client up.
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self.queues)
