"""Trust-gated Agent Reach wrapper.

Agent Reach (Panniantong/Agent-Reach, MIT) routes reads across ~15 platforms
— Twitter/X, Reddit, YouTube, GitHub, Bilibili, XiaoHongShu, RSS, plain web —
by shelling out to whichever upstream backend currently works. For a
prediction-market bot that is a genuine alpha source: sentiment and breaking
discussion move Polymarket prices well before resolution.

It is also, unmodified, a hole straight through CLAUDE.md rule #8. The
upstream SKILL.md tells agents to call `curl`, `gh search`, and `mcporter`
directly. This wrapper exists so that never happens: every target is
classified and routed through `OrchestrationManager.request_scrape` BEFORE
the subprocess runs, exactly like `live_market.scrapling_fetcher`.

Three hard limits, all enforced here rather than by convention:

1. **Gate first.** No `agent-reach` subprocess starts without an approved
   `ScrapeOutcome`. A platform query is gated on the platform's domain.
2. **No auto-install.** `agent-reach install --env=auto` pulls a dozen
   unvetted upstream CLIs (opencli, twitter-cli, rdt-cli, bili-cli, yt-dlp,
   mcporter). None of those passed the trust gate, so we never invoke it.
   `doctor` is read-only and therefore allowed.
3. **Never on the hot path** (CLAUDE.md #14, #16). Each call is a subprocess
   that can take seconds. Research//offline use only — never inside the
   trading loop.

Cookies and session tokens for logged-in platforms stay in Agent Reach's own
config outside this repo. We never read, log, or persist them (#5, #17).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Optional

from agents.orchestration_manager import OrchestrationManager


# Platform → the domain we gate on. A platform read is a scrape of that host,
# so the trust policy must list the domain before the platform is reachable.
PLATFORM_DOMAINS: dict[str, str] = {
    "twitter": "twitter.com",
    "x": "twitter.com",
    "reddit": "reddit.com",
    "youtube": "youtube.com",
    "github": "github.com",
    "bilibili": "bilibili.com",
    "xiaohongshu": "xiaohongshu.com",
    "v2ex": "v2ex.com",
    "linkedin": "linkedin.com",
    "xueqiu": "xueqiu.com",
    "rss": "rss",
    "web": "web",
}

# Subcommands that only read local state. Everything else needs a gated target.
READ_ONLY_COMMANDS = frozenset({"doctor", "check-update", "--version"})

# Never invoked: these mutate the machine or install unvetted third-party CLIs.
FORBIDDEN_COMMANDS = frozenset({"install", "update", "uninstall"})

DEFAULT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class ReachResult:
    target: str
    approved: bool
    audit_id: int | None
    exit_code: int | None
    content_text: str | None
    error: str | None


class AgentReachUnavailable(RuntimeError):
    pass


class AgentReachFetcher:
    """Synchronous, gated façade over the `agent-reach` CLI.

    `runner` is injectable so tests never shell out. Production requires
    `pip install agent-reach` plus whichever backends the user has explicitly
    chosen to install by hand.
    """

    def __init__(
        self,
        manager: OrchestrationManager,
        *,
        agent_id: str = "research",
        runner: Any | None = None,
        binary: str = "agent-reach",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.manager = manager
        self.agent_id = agent_id
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self._runner = runner or self._default_runner

    # ----- public API -----

    def read_url(self, url: str, **kwargs: Any) -> ReachResult:
        """Fetch one URL. Gated on the URL itself."""
        outcome = self.manager.request_scrape(self.agent_id, url)
        if not outcome.approved:
            return ReachResult(
                target=url, approved=False, audit_id=outcome.audit_id,
                exit_code=None, content_text=None, error=outcome.reason,
            )
        return self._invoke(["read", url], target=url, audit_id=outcome.audit_id, **kwargs)

    def search(self, platform: str, query: str, *, limit: int = 10, **kwargs: Any) -> ReachResult:
        """Search one platform. Gated on that platform's domain."""
        key = platform.strip().lower()
        domain = PLATFORM_DOMAINS.get(key)
        if domain is None:
            return ReachResult(
                target=platform, approved=False, audit_id=None, exit_code=None,
                content_text=None,
                error=(
                    f"unknown platform {platform!r}; "
                    f"known: {', '.join(sorted(PLATFORM_DOMAINS))}"
                ),
            )
        # Pseudo-domains ("web", "rss") have no single host to authenticate,
        # so they are not reachable through the gate. Use read_url with the
        # concrete URL instead — that one can be checked.
        if domain in ("web", "rss"):
            return ReachResult(
                target=platform, approved=False, audit_id=None, exit_code=None,
                content_text=None,
                error=(
                    f"{platform!r} has no fixed host to authenticate; "
                    "use read_url(<concrete url>) so the gate can check it"
                ),
            )

        outcome = self.manager.request_scrape(self.agent_id, f"https://{domain}")
        if not outcome.approved:
            return ReachResult(
                target=domain, approved=False, audit_id=outcome.audit_id,
                exit_code=None, content_text=None, error=outcome.reason,
            )
        return self._invoke(
            ["search", key, query, "--limit", str(limit)],
            target=f"{key}:{query}", audit_id=outcome.audit_id, **kwargs,
        )

    def doctor(self) -> ReachResult:
        """Read-only backend health check. No gate needed — touches no target."""
        return self._invoke(["doctor", "--json"], target="doctor", audit_id=None)

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    # ----- internals -----

    def _invoke(
        self,
        args: list[str],
        *,
        target: str,
        audit_id: int | None,
        **kwargs: Any,
    ) -> ReachResult:
        head = args[0] if args else ""
        if head in FORBIDDEN_COMMANDS:
            # Belt and braces: nothing in this class builds such a call, so
            # reaching here means someone passed one in deliberately.
            return ReachResult(
                target=target, approved=True, audit_id=audit_id, exit_code=None,
                content_text=None,
                error=(
                    f"{head!r} is forbidden — it installs unvetted third-party "
                    "CLIs that never passed the trust gate. Install backends "
                    "by hand, one at a time, after review."
                ),
            )
        try:
            code, out, err = self._runner(
                [self.binary, *args], timeout=self.timeout_seconds, **kwargs
            )
        except AgentReachUnavailable as e:
            return ReachResult(
                target=target, approved=True, audit_id=audit_id,
                exit_code=None, content_text=None, error=str(e),
            )
        except Exception as e:
            return ReachResult(
                target=target, approved=True, audit_id=audit_id,
                exit_code=None, content_text=None, error=f"{type(e).__name__}: {e}",
            )
        return ReachResult(
            target=target, approved=True, audit_id=audit_id, exit_code=code,
            content_text=out if code == 0 else None,
            # stderr can echo a cookie-bearing URL, so only the exit code and a
            # truncated message survive into the result.
            error=None if code == 0 else f"exit {code}: {(err or '')[:200]}",
        )

    def _default_runner(self, argv: list[str], *, timeout: int, **_: Any):
        if shutil.which(argv[0]) is None:
            raise AgentReachUnavailable(
                f"{argv[0]!r} not found on PATH. Run `pip install agent-reach`, "
                "then install only the backends you need."
            )
        proc = subprocess.run(          # noqa: S603 — argv list, never shell=True
            argv, capture_output=True, text=True, timeout=timeout, shell=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
