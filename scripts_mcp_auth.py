"""One-time MCP authentication for the fund.

    python scripts_mcp_auth.py robinhood

Opens a browser, you approve, tokens land in ~/.config/lazy-fund/mcp-tokens.json
(0600). After this the daemon connects on its own.

The question this answers, and the only one that matters for unattended
running: **does the server issue a refresh token?** If yes, this is a one-time
step. If no, the fund needs a human on the server's expiry schedule, and it is
better to learn that now than at 3am.
"""

import asyncio
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from trading.mcp_client import FileTokenStorage

SERVERS = {
    "robinhood": "https://agent.robinhood.com/mcp/trading",
    "massive": "https://mcp.massive.com/",
}


async def main(name: str) -> int:
    url = SERVERS.get(name)
    if url is None:
        print(f"unknown server {name!r}; known: {', '.join(SERVERS)}")
        return 2

    storage = FileTokenStorage(url)
    before = storage.summary()
    print(f"server : {url}")
    print(f"tokens : {storage.path}")
    print(f"current: authenticated={before['authenticated']} "
          f"refresh={before['has_refresh_token']}\n")

    try:
        from mcp.client.auth import OAuthClientProvider
        from mcp.client.streamable_http import streamablehttp_client
        from mcp.client.session import ClientSession
        from mcp.shared.auth import OAuthClientMetadata
    except ImportError as e:
        print(f"mcp SDK missing ({e}). Run: uv add mcp")
        return 1

    import webbrowser

    async def redirect(auth_url: str) -> None:
        print(f"opening browser:\n  {auth_url}\n")
        webbrowser.open(auth_url)

    # Capture the redirect with a one-shot local server rather than asking the
    # operator to paste a URL. Hands-off, and it cannot be fat-fingered.
    captured: dict[str, str | None] = {}
    done = asyncio.Event()
    loop = asyncio.get_running_loop()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            q = parse_qs(parsed.query)
            code = (q.get("code") or [""])[0]

            # Only a request that actually carries a code counts. Browsers hit
            # a local server with favicon and prefetch requests, and treating
            # the FIRST request as the callback loses the race to one of those
            # — which surfaces later as a baffling "state mismatch: None".
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
            self.wfile.write(b"<h2>Authorised.</h2><p>You can close this tab and "
                             b"return to the terminal.</p>")
            loop.call_soon_threadsafe(done.set)

        def log_message(self, *a):   # keep the console clean
            pass

    server = HTTPServer(("127.0.0.1", 8900), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    async def callback() -> tuple[str, str | None]:
        print("waiting for the redirect on http://localhost:8900/callback ...")
        try:
            await asyncio.wait_for(done.wait(), timeout=300)
        except asyncio.TimeoutError:
            raise RuntimeError("timed out waiting for the browser redirect")
        finally:
            server.shutdown()
        return captured.get("code", ""), captured.get("state")

    provider = OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(
            client_name="Lazy Fund",
            redirect_uris=["http://localhost:8900/callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=storage,
        redirect_handler=redirect,
        callback_handler=callback,
    )

    try:
        async with streamablehttp_client(url, auth=provider) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                print(f"\nconnected. {len(names)} tools:")
                for n in names[:40]:
                    print("  -", n)
    except Exception as e:
        print(f"\nauth/connect failed: {type(e).__name__}: {e}")
        return 1

    after = storage.summary()
    print(f"\nstored: authenticated={after['authenticated']} "
          f"refresh_token={after['has_refresh_token']}")
    print("UNATTENDED RUNNING: " + (
        "possible — the daemon can refresh on its own."
        if after["has_refresh_token"] else
        "NOT possible without a refresh token; a human must re-auth on expiry."))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "robinhood")))
