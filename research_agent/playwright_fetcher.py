"""Trust-gated Playwright fetcher — the headed browser for JS-rendered pages.

Why this and not the alternatives (evaluated 2026-09-12):

- **`microsoft/playwright-cli`** is a *codegen and inspection* tool — it records
  actions and generates code. It is a developer aid, not a runtime an agent
  drives. The Playwright library is the runtime; that is what this uses.
- **Agent Reach** routes ~15 platforms but needs a dozen backend CLIs installed,
  and CLAUDE.md #20 refuses `install --env=auto` because none of them passed the
  trust gate. With zero backends present it currently fetches nothing.
- **Scrapling** is declared in pyproject but its import fails (missing
  `curl_cffi`), so it fetches nothing either.

So this is the one working path. Same contract as the others: every URL goes
through `OrchestrationManager.request_scrape` BEFORE a browser launches
(CLAUDE.md #8). A blocked target costs no browser at all.

Headed vs headless: `headless=False` is the default because the whole reason to
reach for a browser here is pages that behave differently for automation. Set
`headless=True` for CI, where there is no display.

Never on the hot path (#14, #16) — a page load is seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from agents.orchestration_manager import OrchestrationManager

DEFAULT_TIMEOUT_MS = 30_000
MAX_TEXT_CHARS = 20_000


@dataclass(frozen=True)
class PageResult:
    url: str
    approved: bool
    audit_id: Optional[int]
    status: Optional[int]
    title: Optional[str]
    text: Optional[str]
    error: Optional[str]

    @property
    def ok(self) -> bool:
        """Usable content, not merely a rendered page.

        A 403 still renders — SEC's rate-limit page has a title and body text —
        and treating that as content would feed "Request Rate Threshold
        Exceeded" to a seat as if it were research.
        """
        if not (self.approved and self.error is None and self.text):
            return False
        return self.status is None or 200 <= self.status < 300


@dataclass
class PlaywrightFetcher:
    """Renders a page and returns its visible text. `launcher` is injectable."""

    manager: OrchestrationManager
    agent_id: str = "research"
    headless: bool = False
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    launcher: Any = None

    def fetch(self, url: str, *, wait_for: Optional[str] = None) -> PageResult:
        outcome = self.manager.request_scrape(self.agent_id, url)
        if not outcome.approved:
            # Gate first: a refused target never launches a browser.
            return PageResult(url, False, outcome.audit_id, None, None, None,
                              outcome.reason)
        try:
            return self._render(url, outcome.audit_id, wait_for)
        except Exception as e:
            return PageResult(url, True, outcome.audit_id, None, None, None,
                              f"{type(e).__name__}: {e}"[:300])

    # ---------- internals ----------

    def _render(self, url: str, audit_id: Optional[int],
                wait_for: Optional[str]) -> PageResult:
        launch = self.launcher or self._default_launcher
        with launch() as browser:
            page = browser.new_page()
            try:
                response = page.goto(url, timeout=self.timeout_ms,
                                     wait_until="domcontentloaded")
                if wait_for:
                    page.wait_for_selector(wait_for, timeout=self.timeout_ms)
                status = response.status if response is not None else None
                text = (page.inner_text("body") or "")[:MAX_TEXT_CHARS]
                return PageResult(url, True, audit_id, status, page.title(),
                                  text, None)
            finally:
                page.close()

    def _default_launcher(self):
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self.headless)
                try:
                    yield browser
                finally:
                    browser.close()

        return _ctx()
