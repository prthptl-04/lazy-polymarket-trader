"""Interactive OAuth for an MCP server, driven from the dashboard.

`scripts_mcp_auth.py` does this from a terminal. This does the same flow from
the running daemon so the operator can re-authorise from the GO button instead
of finding a shell — which matters because the failure it fixes is silent: a
token lapses, the daemon correctly refuses to open a browser, and the fund runs
on with no venue quotes until somebody notices.

Shape of the flow, and why it is split in two:

    begin()   starts the OAuth exchange in the background and returns the
              authorisation URL as soon as the SDK produces it
    status()  says whether the browser has come back yet

The UI opens the URL itself rather than the server calling `webbrowser.open`.
The operator's browser is already in front of them; the daemon's idea of a
default browser may be a headless box's idea of one.

**Only known servers.** The URL is chosen from `SERVERS` by name, never taken
from the request — an OAuth flow pointed at an attacker's URL would hand them a
brokerage authorisation.

**No token material is ever returned.** `status()` reports whether a session
exists, not what it contains.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

from trading.mcp_client import FileTokenStorage

logger = logging.getLogger(__name__)

SERVERS: dict[str, str] = {
    "robinhood": "https://agent.robinhood.com/mcp/trading",
    "massive": "https://mcp.massive.com/",
}

CALLBACK_PORT = 8900
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"
# How long the operator has to finish in the browser before we give up and free
# the port. Long enough for a password manager and an MFA prompt.
BROWSER_TIMEOUT_SECONDS = 300.0


@dataclass
class AuthFlow:
    """One in-flight authorisation. Not reusable — start a new one per attempt."""

    venue: str
    server_url: str
    token_path: Optional[str] = None

    authorize_url: Optional[str] = None
    error: Optional[str] = None
    done: bool = False

    _url_ready: Any = field(default=None, init=False)
    _task: Any = field(default=None, init=False)
    _server: Any = field(default=None, init=False)

    def summary(self) -> dict:
        return {"venue": self.venue, "authorize_url": self.authorize_url,
                "done": self.done, "error": self.error,
                "in_progress": bool(self._task) and not self.done}


async def begin(venue: str, token_path: Optional[str] = None) -> AuthFlow:
    """Start the exchange and return once the authorisation URL exists.

    Returns with `error` set rather than raising: a failed re-auth must leave
    the operator looking at a reason, not at a 500.
    """
    server_url = SERVERS.get(venue)
    if server_url is None:
        flow = AuthFlow(venue=venue, server_url="")
        flow.error = f"unknown venue {venue!r}; known: {', '.join(SERVERS)}"
        flow.done = True
        return flow

    flow = AuthFlow(venue=venue, server_url=server_url, token_path=token_path)
    flow._url_ready = asyncio.Event()
    flow._task = asyncio.create_task(_run(flow), name=f"mcp-auth:{venue}")
    try:
        await asyncio.wait_for(flow._url_ready.wait(), timeout=30.0)
    except asyncio.TimeoutError:
        flow.error = "the server did not return an authorisation URL in 30s"
        flow.done = True
    return flow


async def _run(flow: AuthFlow) -> None:
    """The whole exchange, start to stored tokens."""
    try:
        from mcp.client.auth import OAuthClientProvider
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        from mcp.shared.auth import OAuthClientMetadata
    except ImportError as e:
        flow.error = f"mcp SDK missing ({e})"
        flow.done = True
        flow._url_ready.set()
        return

    loop = asyncio.get_running_loop()
    captured: dict[str, Optional[str]] = {}
    arrived = asyncio.Event()

    async def redirect(auth_url: str) -> None:
        # Hand the URL out; do NOT open a browser here. The daemon may have no
        # display, and the operator's browser is already open in front of them.
        flow.authorize_url = auth_url
        flow._url_ready.set()

    server = _callback_server(captured, arrived, loop)
    flow._server = server

    async def callback() -> tuple[str, Optional[str]]:
        try:
            await asyncio.wait_for(arrived.wait(), timeout=BROWSER_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"no redirect within {BROWSER_TIMEOUT_SECONDS:.0f}s — "
                "the browser tab was probably closed")
        return captured.get("code", ""), captured.get("state")

    storage = FileTokenStorage(flow.server_url, path=flow.token_path)
    provider = OAuthClientProvider(
        server_url=flow.server_url,
        client_metadata=OAuthClientMetadata(
            client_name="Lazy Fund",
            redirect_uris=[CALLBACK_URL],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=storage,
        redirect_handler=redirect,
        callback_handler=callback,
    )

    try:
        async with streamablehttp_client(flow.server_url, auth=provider) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                await session.list_tools()      # prove the token actually works
    except Exception as e:
        # Never log the exception body: an OAuth failure can echo the request,
        # and the request carries the code.
        flow.error = f"authorisation failed: {type(e).__name__}"
        logger.warning("MCP auth for %s failed: %s", flow.venue, type(e).__name__)
    finally:
        flow.done = True
        flow._url_ready.set()
        try:
            server.shutdown()
        except Exception:
            logger.debug("callback server shutdown was not clean", exc_info=True)


def _callback_server(captured: dict, arrived: asyncio.Event, loop: Any) -> HTTPServer:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:                               # noqa: N802
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            code = (q.get("code") or [""])[0]
            # Only a request carrying a code counts. Browsers hit a local
            # server with favicon and prefetch requests, and treating the first
            # one as the callback loses the race — which surfaces later as a
            # baffling "state mismatch: None".
            if not code:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"waiting for the authorisation redirect")
                return
            captured["code"] = code
            captured["state"] = (q.get("state") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<body style='font:16px system-ui;padding:3rem'>"
                b"<h2>Authorised.</h2><p>Close this tab \xe2\x80\x94 the fund "
                b"dashboard will start the engine.</p></body>")
            loop.call_soon_threadsafe(arrived.set)

        def log_message(self, *a: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
