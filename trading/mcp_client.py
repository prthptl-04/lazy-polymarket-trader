"""MCP client the fund owns, so the daemon is not tied to a Claude Code session.

The blocker this removes rested on a false premise. "Robinhood is MCP-only" was
read as "only Claude Code can reach it", but MCP over HTTP is JSON-RPC plus
OAuth — any client can hold a session. The Python SDK ships an OAuth client,
PKCE, and pluggable token storage, so the fund can authenticate for itself.

What that changes operationally:

- Robinhood's desktop-browser OAuth is a **one-time** step, not a per-run one,
  **provided the server issues a refresh token**. `FileTokenStorage.summary()`
  reports whether one was issued, because that single fact decides whether
  unattended running is possible at all. If it was not, the honest answer is
  that the fund needs a human to re-authenticate on the server's schedule, and
  the scheduler should say so rather than dying at 3am.
- Tokens live in a gitignored file with 0600 permissions, are never logged, and
  are never returned by any accessor that renders to a UI (CLAUDE.md #5, #17).

`discover_tools()` exists because Robinhood does not publish its tool schema;
it is the difference between a guessed `TOOL_NAMES` map and a verified one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_PATH = Path.home() / ".config" / "lazy-fund" / "mcp-tokens.json"


class FileTokenStorage:
    """Token store for `mcp.client.auth.OAuthClientProvider`.

    One file per server URL. Written 0600 — an OAuth token for a brokerage is a
    bearer credential, and a world-readable one is the same problem as a
    committed private key.
    """

    def __init__(self, server_url: str, path: Optional[Path] = None) -> None:
        self.server_url = server_url
        self.path = Path(path or os.environ.get("MCP_TOKEN_PATH", DEFAULT_TOKEN_PATH))
        self._cache: dict[str, Any] = {}

    # -- mcp.client.auth.TokenStorage protocol --

    async def get_tokens(self) -> Any:
        # Rehydrate into the SDK's model. Returning the raw dict we persisted
        # fails deep inside the OAuth flow with "'dict' object has no attribute
        # 'client_id'" — the storage protocol is typed, not just JSON-shaped.
        return _model("OAuthToken", self._load().get("tokens"))

    async def set_tokens(self, tokens: Any) -> None:
        self._save("tokens", tokens)

    async def get_client_info(self) -> Any:
        return _model("OAuthClientInformationFull", self._load().get("client_info"))

    async def set_client_info(self, info: Any) -> None:
        self._save("client_info", info)

    # -- operator-facing --

    def summary(self) -> dict:
        """Whether we hold a session, and whether it can renew itself.

        NEVER returns token material. `has_refresh_token` is the load-bearing
        field: without one, unattended running is impossible and the operator
        needs to know that before they rely on it.
        """
        data = self._load()
        tokens = data.get("tokens") or {}
        if not isinstance(tokens, dict):
            tokens = getattr(tokens, "__dict__", {}) or {}
        return {
            "server_url": self.server_url,
            "authenticated": bool(tokens.get("access_token")),
            "has_refresh_token": bool(tokens.get("refresh_token")),
            "expires_in": tokens.get("expires_in"),
            "scope": tokens.get("scope"),
            "path": str(self.path),
        }

    def clear(self) -> None:
        self._cache = {}
        if self.path.exists():
            self.path.unlink()

    # -- internals --

    def _all(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _load(self) -> dict:
        return self._all().get(self.server_url, {})

    def _save(self, key: str, value: Any) -> None:
        payload = self._all()
        entry = payload.setdefault(self.server_url, {})
        entry[key] = _jsonable(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2))
        # Bearer credentials for a brokerage account: owner-only.
        self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@dataclass
class McpSession:
    """A long-lived MCP connection with tool discovery and calls.

    `session_factory` is injectable so tests never touch the network.
    """

    server_url: str
    storage: Optional[FileTokenStorage] = None
    session_factory: Any = None
    timeout_seconds: float = 60.0
    _session: Any = field(default=None, init=False)
    _tools: dict[str, dict] = field(default_factory=dict, init=False)
    _stack: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.storage = self.storage or FileTokenStorage(self.server_url)

    @property
    def connected(self) -> bool:
        return self._session is not None

    def auth_summary(self) -> dict:
        return self.storage.summary()

    async def connect(self) -> bool:
        """Open the session. Returns False rather than raising when we have no
        credentials — an unauthenticated venue must degrade, not crash a cycle."""
        if self._session is not None:
            return True
        if self.session_factory is None and not self.storage.summary()["authenticated"]:
            logger.info("MCP %s: no stored credentials; run the auth flow once",
                        self.server_url)
            return False
        try:
            self._session = await self._open()
        except Exception as e:
            logger.warning("MCP %s: connect failed (%s)", self.server_url, type(e).__name__)
            return False
        return True

    async def discover_tools(self) -> dict[str, dict]:
        """The live tool surface. Robinhood does not publish a schema, so this
        is how a guessed name map becomes a verified one."""
        if not await self.connect():
            return {}
        try:
            listed = await asyncio.wait_for(self._session.list_tools(),
                                            timeout=self.timeout_seconds)
        except Exception:
            logger.exception("MCP %s: list_tools failed", self.server_url)
            return {}
        tools = getattr(listed, "tools", listed) or []
        self._tools = {
            t.name if hasattr(t, "name") else t["name"]: {
                "description": getattr(t, "description", None) or "",
                "schema": getattr(t, "inputSchema", None) or {},
            }
            for t in tools
        }
        return self._tools

    async def call(self, tool: str, arguments: Optional[dict] = None) -> Any:
        if not await self.connect():
            raise McpUnavailable(f"{self.server_url}: not authenticated")
        result = await asyncio.wait_for(
            self._session.call_tool(tool, arguments or {}),
            timeout=self.timeout_seconds,
        )
        return _unwrap(result)

    async def close(self) -> None:
        self._session = None
        stack, self._stack = self._stack, None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                # A server that has already dropped the connection makes the
                # unwind noisy; the session is gone either way.
                logger.debug("MCP unwind was not clean", exc_info=True)

    async def _open(self) -> Any:
        if self.session_factory is not None:
            return await self.session_factory(self)
        return await self._open_real()

    async def _open_real(self) -> Any:
        """Open a real streamable-HTTP session and KEEP it open.

        `streamablehttp_client` and `ClientSession` are async context managers.
        Re-entering them per call would pay a TLS handshake and an MCP
        initialize round trip every time — a round table makes seven calls per
        candidate. An AsyncExitStack holds them open for the daemon's lifetime
        and `close()` unwinds them.
        """
        from contextlib import AsyncExitStack

        from mcp.client.auth import OAuthClientProvider
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        from mcp.shared.auth import OAuthClientMetadata

        async def _no_browser(url: str) -> None:
            raise McpUnavailable(
                "this session needs interactive authorisation; run "
                "`python scripts_mcp_auth.py` once from a desktop"
            )

        async def _no_callback() -> tuple[str, Optional[str]]:
            raise McpUnavailable("interactive authorisation required")

        provider = OAuthClientProvider(
            server_url=self.server_url,
            client_metadata=OAuthClientMetadata(
                client_name="Lazy Fund",
                redirect_uris=["http://localhost:8900/callback"],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            ),
            storage=self.storage,
            redirect_handler=_no_browser,
            callback_handler=_no_callback,
        )

        stack = AsyncExitStack()
        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(self.server_url, auth=provider))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._stack = stack
        return session


class McpUnavailable(RuntimeError):
    """Raised when a call is attempted without a usable session."""


class McpError(RuntimeError):
    """A tool the server refused to run.

    Raised rather than returned, because the failure mode of returning it was
    much worse than a crash: a refusal arrives as a plain text block, and
    `RobinhoodVenue.positions` routes that through `_rows`, which cannot find
    a key in a string and returns `[]`. **An authorization error read as a flat
    account** — so the fund would conclude it holds nothing and re-buy
    everything. `place_order` and `account` meanwhile called `.get()` on the
    string and raised AttributeError outside their own `try`, turning a venue
    rejection into a cycle exception instead of an `OrderAck(rejected)`.
    """


def _unwrap(result: Any) -> Any:
    """Pull the payload out of an MCP tool result.

    Content is a list of blocks; text blocks usually carry JSON. Parsing it
    here keeps every adapter from re-implementing the same unwrap — and so does
    raising on `isError`, which is why the check belongs here and not in each
    of the four call sites.
    """
    content = getattr(result, "content", None)
    if getattr(result, "isError", False):
        raise McpError(_error_text(content) or "the MCP server refused the call")
    if content is None:
        return result
    out = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            out.append(json.loads(text))
        except (ValueError, TypeError):
            out.append(text)
    if not out:
        return None
    return out[0] if len(out) == 1 else out


def _error_text(content: Any) -> str:
    """Whatever the server said, for the exception message."""
    if not content:
        return ""
    parts = [t for t in (getattr(b, "text", None) for b in content) if t]
    return " ".join(parts)[:400]


def _model(name: str, data: Any) -> Any:
    """Rebuild an SDK auth model from stored JSON; None when absent."""
    if not data:
        return None
    if not isinstance(data, dict):
        return data
    try:
        import mcp.shared.auth as auth_models
        return getattr(auth_models, name)(**data)
    except Exception:
        logger.warning("could not rehydrate %s from storage", name)
        return None


def _jsonable(value: Any) -> Any:
    """Coerce SDK models to plain JSON.

    `model_dump()` alone is not enough: pydantic keeps nested types like AnyUrl,
    which json.dumps refuses. `mode="json"` is what actually makes it round-trip,
    and getting this wrong fails at the very end of an OAuth flow — after the
    browser round trip, which is the most annoying place to discover it.
    """
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    fn = getattr(value, "model_dump", None)
    if callable(fn):
        try:
            return fn(mode="json")
        except TypeError:
            pass            # pydantic v1 has no mode=
        except Exception:
            pass
    for attr in ("model_dump", "dict"):
        f = getattr(value, attr, None)
        if callable(f):
            try:
                return json.loads(json.dumps(f(), default=str))
            except Exception:
                pass
    return json.loads(json.dumps(getattr(value, "__dict__", str(value)), default=str))
