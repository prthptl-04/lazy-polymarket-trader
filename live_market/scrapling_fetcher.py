"""Trust-gated Scrapling wrapper.

Scrapling (D4Vinci/Scrapling, BSD-3-Clause) is excellent at JS-heavy market
pages and anti-bot bypass. We keep our trust gate in front of it: every URL
routes through `OrchestrationManager.request_scrape` BEFORE Scrapling touches
the network.

Two modes:

- `fetch_static(url)` — fast HTTP fetcher. Use for plain HTML / JSON endpoints.
- `fetch_dynamic(url)` — headed Chromium via Scrapling's stealth session.
  Use only when JS execution is required (Cloudflare-protected pages,
  React-rendered market detail pages).

Both return a `FetchResult` with content + URL audit info. We never call into
Scrapling without an approved `ScrapeOutcome`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from agents.orchestration_manager import OrchestrationManager


@dataclass(frozen=True)
class FetchResult:
    url: str
    approved: bool
    audit_id: int | None
    status_code: int | None
    content_text: str | None
    error: str | None


class ScraplingFetcher:
    """Synchronous Scrapling wrapper for use outside the asyncio loop.

    The Scrapling library itself is imported lazily so tests don't need
    a browser install. Production calls require `pip install scrapling`
    (already in pyproject) and `scrapling install` (one-time Chromium).
    """

    def __init__(
        self,
        manager: OrchestrationManager,
        *,
        agent_id: str = "research",
        fetcher_factory: Any | None = None,
        stealth_factory: Any | None = None,
    ) -> None:
        self.manager = manager
        self.agent_id = agent_id
        self._fetcher_factory = fetcher_factory
        self._stealth_factory = stealth_factory

    # ----- public API -----

    def fetch_static(self, url: str, **kwargs: Any) -> FetchResult:
        outcome = self.manager.request_scrape(self.agent_id, url)
        if not outcome.approved:
            return FetchResult(
                url=url, approved=False, audit_id=outcome.audit_id,
                status_code=None, content_text=None, error=outcome.reason,
            )
        try:
            fetcher = self._resolve_static_fetcher()
            page = fetcher.get(url, **kwargs)
            return FetchResult(
                url=url, approved=True, audit_id=outcome.audit_id,
                status_code=_status_of(page), content_text=_text_of(page), error=None,
            )
        except Exception as e:
            return FetchResult(
                url=url, approved=True, audit_id=outcome.audit_id,
                status_code=None, content_text=None, error=f"{type(e).__name__}: {e}",
            )

    def fetch_dynamic(self, url: str, **kwargs: Any) -> FetchResult:
        """Headed Chromium fetch via StealthySession. Slow but bypasses Cloudflare."""
        outcome = self.manager.request_scrape(self.agent_id, url)
        if not outcome.approved:
            return FetchResult(
                url=url, approved=False, audit_id=outcome.audit_id,
                status_code=None, content_text=None, error=outcome.reason,
            )
        try:
            session_cls = self._resolve_stealth_session()
            with session_cls() as session:
                page = session.fetch(url, **kwargs)
                return FetchResult(
                    url=url, approved=True, audit_id=outcome.audit_id,
                    status_code=_status_of(page), content_text=_text_of(page), error=None,
                )
        except Exception as e:
            return FetchResult(
                url=url, approved=True, audit_id=outcome.audit_id,
                status_code=None, content_text=None, error=f"{type(e).__name__}: {e}",
            )

    # ----- internals -----

    def _resolve_static_fetcher(self) -> Any:
        if self._fetcher_factory is not None:
            return self._fetcher_factory
        try:
            from scrapling.fetchers import Fetcher  # type: ignore
            return Fetcher
        except ImportError as e:
            raise RuntimeError(
                "scrapling is not installed. Run `uv sync` to install."
            ) from e

    def _resolve_stealth_session(self) -> Any:
        if self._stealth_factory is not None:
            return self._stealth_factory
        try:
            from scrapling.fetchers import StealthySession  # type: ignore
            return StealthySession
        except ImportError as e:
            raise RuntimeError(
                "scrapling stealth extras not installed. Run `uv sync` then `scrapling install`."
            ) from e


def _status_of(page: Any) -> int | None:
    return getattr(page, "status", None)


def _text_of(page: Any) -> str | None:
    body = getattr(page, "body", None)
    if isinstance(body, bytes):
        try:
            return body.decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(body, str):
        return body
    text = getattr(page, "text", None)
    return text if isinstance(text, str) else None
