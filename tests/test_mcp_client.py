"""The fund's own MCP client — removes the Claude-Code-session dependency.

The blocker rested on a false premise: MCP over HTTP is JSON-RPC plus OAuth, so
any client can hold a session. These tests pin the safety properties of holding
a brokerage bearer token in a daemon.

- Blind: a token must NEVER appear in anything the UI or a log can render.
- Blind: the token file must be owner-only (0600).
- Blind: `has_refresh_token` decides whether unattended running is possible at
  all, so it must be reported honestly.
- Edge: no credentials degrades, it does not crash a cycle.
"""

import asyncio
import json
import os
import stat

import pytest

from trading.mcp_client import (
    FileTokenStorage,
    McpSession,
    McpUnavailable,
    _unwrap,
)

URL = "https://agent.robinhood.com/mcp/trading"


@pytest.fixture
def storage(tmp_path):
    return FileTokenStorage(URL, path=tmp_path / "tok.json")


# ---------------- credential safety ----------------

@pytest.mark.asyncio
async def test_summary_never_contains_token_material(storage):
    await storage.set_tokens({"access_token": "SUPERSECRET",
                              "refresh_token": "REFRESHSECRET", "expires_in": 3600})
    blob = json.dumps(storage.summary())
    assert "SUPERSECRET" not in blob and "REFRESHSECRET" not in blob


@pytest.mark.asyncio
async def test_token_file_is_owner_only(storage):
    await storage.set_tokens({"access_token": "x"})
    mode = stat.S_IMODE(os.stat(storage.path).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


@pytest.mark.asyncio
async def test_refresh_token_presence_is_reported_honestly(storage):
    """This single field decides whether the fund can run unattended."""
    await storage.set_tokens({"access_token": "x"})
    assert storage.summary()["has_refresh_token"] is False

    await storage.set_tokens({"access_token": "x", "refresh_token": "y"})
    assert storage.summary()["has_refresh_token"] is True


def test_no_tokens_reads_as_unauthenticated(storage):
    s = storage.summary()
    assert s["authenticated"] is False and s["has_refresh_token"] is False


@pytest.mark.asyncio
async def test_servers_are_stored_separately(tmp_path):
    a = FileTokenStorage("https://a.test/mcp", path=tmp_path / "t.json")
    b = FileTokenStorage("https://b.test/mcp", path=tmp_path / "t.json")
    await a.set_tokens({"access_token": "A"})
    await b.set_tokens({"access_token": "B"})
    assert (await a.get_tokens())["access_token"] == "A"
    assert (await b.get_tokens())["access_token"] == "B"


@pytest.mark.asyncio
async def test_clear_removes_the_file(storage):
    await storage.set_tokens({"access_token": "x"})
    storage.clear()
    assert storage.summary()["authenticated"] is False


def test_corrupt_token_file_degrades(tmp_path):
    p = tmp_path / "t.json"
    p.write_text("{ not json")
    assert FileTokenStorage(URL, path=p).summary()["authenticated"] is False


@pytest.mark.asyncio
async def test_pydantic_style_tokens_are_serialised(storage):
    class _Tok:
        def model_dump(self): return {"access_token": "x", "refresh_token": "y"}
    await storage.set_tokens(_Tok())
    assert storage.summary()["has_refresh_token"] is True


# ---------------- session behaviour ----------------

@pytest.mark.asyncio
async def test_unauthenticated_connect_degrades_rather_than_raising(storage):
    """A venue without credentials must not take down a trading cycle."""
    s = McpSession(server_url=URL, storage=storage)
    assert await s.connect() is False
    assert s.connected is False


@pytest.mark.asyncio
async def test_call_without_a_session_raises_clearly(storage):
    s = McpSession(server_url=URL, storage=storage)
    with pytest.raises(McpUnavailable):
        await s.call("get_accounts")


class _FakeSession:
    def __init__(self, tools=(), result=None):
        self._tools, self._result = tools, result
        self.calls = []

    async def list_tools(self):
        class _T:
            def __init__(self, n): self.name, self.description, self.inputSchema = n, "d", {}
        class _R: pass
        r = _R(); r.tools = [_T(n) for n in self._tools]
        return r

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return self._result


def _session(storage, fake):
    async def factory(_): return fake
    return McpSession(server_url=URL, storage=storage, session_factory=factory)


@pytest.mark.asyncio
async def test_discover_tools_returns_the_live_surface(storage):
    fake = _FakeSession(tools=("get_accounts", "place_equity_order"))
    tools = await _session(storage, fake).discover_tools()
    assert set(tools) == {"get_accounts", "place_equity_order"}


@pytest.mark.asyncio
async def test_call_passes_arguments_through(storage):
    class _Block: text = '{"ok": true}'
    class _Res: content = [_Block()]
    fake = _FakeSession(result=_Res())
    out = await _session(storage, fake).call("get_accounts", {"a": 1})
    assert out == {"ok": True}
    assert fake.calls == [("get_accounts", {"a": 1})]


# ---------------- result unwrapping ----------------

def test_unwrap_parses_json_text_blocks():
    class _B: text = '{"x": 1}'
    class _R: content = [_B()]
    assert _unwrap(_R()) == {"x": 1}


def test_unwrap_keeps_plain_text():
    class _B: text = "hello"
    class _R: content = [_B()]
    assert _unwrap(_R()) == "hello"


def test_unwrap_handles_empty_content():
    class _R: content = []
    assert _unwrap(_R()) is None


def test_unwrap_returns_multiple_blocks_as_a_list():
    class _B:
        def __init__(self, t): self.text = t
    class _R: content = [_B('{"a":1}'), _B('{"b":2}')]
    assert _unwrap(_R()) == [{"a": 1}, {"b": 2}]
