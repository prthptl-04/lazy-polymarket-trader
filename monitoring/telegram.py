"""Telegram notifications for what the fund actually did.

The idea is borrowed from HOODRADAR's notification bridge; none of its code is.
That project is a research desk for Robinhood *Chain* — an EVM L2 — and its
on-chain machinery has nothing to do with a brokerage account.

Three rules, and each of them is a way a notifier turns into a liability:

**It must never block a cycle.** A trading loop that waits on a chat API has
made Telegram a dependency of execution. Every send is fire-and-forget off the
hot path (rules #14 and #16), and a dead network loses a message rather than a
trade.

**It must never leak the token.** The token is a bearer credential: anyone
holding it can post as the bot. Read from the environment, never logged, never
placed in an error message, redacted in `repr` — the same stance rule #17 takes
with the user-channel credentials.

**It must never claim more than it knows.** A notification reports what the
venue said. It does not put a P&L on an entry, and it does not round a fill
into something prettier than the fill — a message that disagrees with the trade
ledger is worse than no message, because the ledger is the one that matters.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SECONDS = 8.0


def _post(url: str, payload: dict) -> None:
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS):  # noqa: S310 — https, api.telegram.org
        return None


@dataclass
class TelegramNotifier:
    """Fire-and-forget notifications. Absent configuration means simply off."""

    token: Optional[str] = None
    chat_id: Optional[str] = None
    send: Callable[[str, dict], None] = _post
    # Off by default: a notifier that blocks is the failure this exists to
    # avoid, and tests want the send to have happened by the time they assert.
    background: bool = False

    @classmethod
    def from_env(cls, **kw) -> "TelegramNotifier":
        return cls(token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
                   chat_id=os.getenv("TELEGRAM_CHAT_ID") or None, **kw)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def notify(self, text: str) -> None:
        """Send, or quietly do nothing. Never raises."""
        if not self.enabled or not text:
            return
        url = API.format(token=self.token)
        payload = {"chat_id": self.chat_id, "text": text,
                   "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if self.background:
            threading.Thread(target=self._send_quietly, args=(url, payload),
                             daemon=True).start()
        else:
            self._send_quietly(url, payload)

    def _send_quietly(self, url: str, payload: dict) -> None:
        try:
            self.send(url, payload)
        except Exception:
            # Deliberately not `logger.exception`: the URL carries the token,
            # and a traceback would put a bearer credential in the log file.
            logger.warning("telegram notification failed (message dropped)")

    def __repr__(self) -> str:
        return (f"TelegramNotifier(enabled={self.enabled}, "
                f"chat_id={self.chat_id!r}, token=<redacted>)")

    __str__ = __repr__


def format_fill(fill: dict) -> str:
    """One line per fill, saying only what the venue reported.

    The mode is shouted rather than mentioned. A paper fill read as a real one
    is the single worst thing a notification here can do, and it is exactly the
    mistake a glance at a phone makes easy.
    """
    side = str(fill.get("side") or "").upper()
    mode = str(fill.get("mode") or "live").upper()
    tag = "📝 PAPER" if mode == "PAPER" else "💰 LIVE"
    arrow = "🟢" if side == "BUY" else "🔴"
    symbol = fill.get("symbol") or "?"
    quantity = fill.get("quantity")
    price = fill.get("price")

    parts = [f"{arrow} <b>{side} {symbol}</b> · {tag}",
             f"{_plain(quantity)} @ {_plain(price)}"]

    reason = fill.get("reason")
    if reason:
        parts.append(f"reason: {reason}")

    # Only on an exit. There is no P&L on an entry, and inventing one is how a
    # notification starts lying before the position has done anything.
    realized = fill.get("realized_usd")
    if realized is not None:
        parts.append(f"realised: {realized:+.2f} USD")

    venue = fill.get("venue")
    if venue:
        parts.append(f"venue: {venue}")
    return "\n".join(parts)


def _plain(value: Any) -> str:
    """Numbers as they were, not as they would look nicer.

    A message that disagrees with the trade ledger is worse than no message.
    """
    if value is None:
        return "?"
    if isinstance(value, float):
        return f"{value:.8f}".rstrip("0").rstrip(".")
    return str(value)
