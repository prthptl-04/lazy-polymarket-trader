"""Trust-gated Playwright fetcher.

Chosen over the alternatives after checking all three (2026-09-12):
playwright-cli is a codegen tool not a runtime; Agent Reach has no backends
installed and CLAUDE.md #20 refuses to auto-install them; Scrapling's import
fails on a missing dependency.

- Blind: a blocked URL must never launch a browser.
- Blind: a rendered error page is NOT ok — a 403 body would otherwise reach a
  seat as research.
"""

import pytest

from research_agent.playwright_fetcher import PageResult, PlaywrightFetcher


class _Outcome:
    def __init__(self, approved, reason="", audit_id=1):
        self.approved, self.reason, self.audit_id = approved, reason, audit_id


class _Manager:
    def __init__(self, approved=True):
        self.approved, self.calls = approved, []

    def request_scrape(self, agent_id, target):
        self.calls.append(target)
        return _Outcome(self.approved, "not allowlisted")


class _Page:
    def __init__(self, status=200, text="hello world", title="T"):
        self._status, self._text, self._title = status, text, title
        self.waited = None

    def goto(self, url, **kw):
        class _R: status = self._status
        return _R()

    def wait_for_selector(self, sel, **kw): self.waited = sel
    def inner_text(self, sel): return self._text
    def title(self): return self._title
    def close(self): pass


class _Browser:
    def __init__(self, page): self._page = page
    def new_page(self): return self._page


def _launcher(page):
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        yield _Browser(page)
    return lambda: _ctx()


def _fetcher(manager, page, **kw):
    return PlaywrightFetcher(manager=manager, launcher=_launcher(page), **kw)


# ---------------- the gate ----------------

def test_blocked_url_never_launches_a_browser():
    m = _Manager(approved=False)

    def _boom():
        raise AssertionError("browser must not launch")

    r = PlaywrightFetcher(manager=m, launcher=_boom).fetch("https://evil.example.com")
    assert r.approved is False and not r.ok
    assert "not allowlisted" in r.error


def test_approved_url_renders():
    m = _Manager()
    r = _fetcher(m, _Page()).fetch("https://www.sec.gov/")
    assert r.ok and r.text == "hello world" and r.title == "T"
    assert m.calls == ["https://www.sec.gov/"]


# ---------------- error pages are not content ----------------

@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_rendered_error_page_is_not_ok(status):
    """A 403 body still renders; feeding it to a seat as research is the bug."""
    r = _fetcher(_Manager(), _Page(status=status, text="Rate Threshold Exceeded")).fetch("https://x.com")
    assert r.approved is True          # the gate allowed it
    assert not r.ok                    # but it is not usable content


def test_empty_body_is_not_ok():
    assert not _fetcher(_Manager(), _Page(text="")).fetch("https://x.com").ok


def test_missing_status_still_counts_as_ok():
    """Some navigations report no response object; text is the signal then."""
    assert _fetcher(_Manager(), _Page(status=None)).fetch("https://x.com").ok


# ---------------- behaviour ----------------

def test_wait_for_selector_is_passed_through():
    page = _Page()
    _fetcher(_Manager(), page).fetch("https://x.com", wait_for="#main")
    assert page.waited == "#main"


def test_render_failure_is_contained():
    class _Exploding(_Page):
        def goto(self, url, **kw): raise RuntimeError("net::ERR_FAILED")

    r = _fetcher(_Manager(), _Exploding()).fetch("https://x.com")
    assert r.approved is True and not r.ok and "ERR_FAILED" in r.error


def test_text_is_truncated():
    from research_agent.playwright_fetcher import MAX_TEXT_CHARS
    r = _fetcher(_Manager(), _Page(text="x" * 999_999)).fetch("https://x.com")
    assert len(r.text) == MAX_TEXT_CHARS


def test_headed_is_the_default():
    """The reason to use a browser at all is pages that detect automation."""
    assert PlaywrightFetcher(manager=_Manager()).headless is False
