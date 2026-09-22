"""The MCP session must be entered and left by ONE task.

What happened, from the live log:

    RuntimeError: Attempted to exit cancel scope in a different task than it
    was entered in

`streamablehttp_client` opens an anyio task group. anyio requires its
`__aenter__` and `__aexit__` to run in the SAME task. The session was opened
lazily by whichever task first needed a quote — a fund cycle — and then held on
a long-lived object, so it was torn down by a different task: a STOP cancelling
the cycle, or a later `close()`. That crashed the loop, and with the quote
source gone the fund could not run at all.

The fix is a supervisor: one task owns the context for its whole life and holds
it open until asked to shut down. Calls still come from any task, which is
fine — it is the teardown that anyio pins to a task, not the use.
"""

import asyncio

import pytest

from trading.mcp_client import McpSession


class _FakeSession:
    """Records which task entered and exited, which is the whole point."""
    def __init__(self, log): self.log, self.closed = log, False
    async def initialize(self): pass
    async def list_tools(self): return type("R", (), {"tools": []})()
    async def call_tool(self, tool, args): return {"tool": tool}


def _session(log):
    async def factory(client):
        log.append(("enter", asyncio.current_task().get_name()))
        client._on_close = lambda: log.append(
            ("exit", asyncio.current_task().get_name()))
        return _FakeSession(log)
    return McpSession(server_url="https://example.test/mcp",
                      session_factory=factory)


def test_enter_and_exit_happen_in_the_same_task():
    """The exact anyio requirement that was being violated."""
    log = []

    async def go():
        client = _session(log)
        assert await client.connect()
        await client.call("get_accounts")
        await client.close()

    asyncio.run(go())
    entered = [name for kind, name in log if kind == "enter"]
    exited = [name for kind, name in log if kind == "exit"]
    assert entered and exited, log
    assert entered[0] == exited[0], f"entered in {entered[0]}, exited in {exited[0]}"


def test_a_cancelled_caller_does_not_take_the_session_with_it():
    """The production failure. A STOP cancels the fund cycle; the cycle is
    where `connect()` first happened, so the session died with it."""
    log = []

    async def go():
        client = _session(log)

        async def cycle():
            await client.connect()
            await asyncio.sleep(10)          # cancelled here, mid-life

        task = asyncio.create_task(cycle())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # The session must still be usable by the next cycle.
        assert client.connected
        assert await client.call("get_accounts") == {"tool": "get_accounts"}
        await client.close()

    asyncio.run(go())


def test_close_is_idempotent_and_never_raises():
    async def go():
        client = _session([])
        await client.connect()
        await client.close()
        await client.close()
        assert not client.connected

    asyncio.run(go())


def test_reconnecting_after_a_close_works():
    """A dropped stream must not permanently disable the venue."""
    log = []

    async def go():
        client = _session(log)
        await client.connect()
        await client.close()
        assert await client.connect()
        await client.call("get_accounts")
        await client.close()

    asyncio.run(go())
    assert len([k for k, _ in log if k == "enter"]) == 2


def test_an_unauthenticated_session_degrades_rather_than_raising():
    """An unauthenticated venue must not crash a cycle."""
    async def go():
        client = McpSession(server_url="https://example.test/mcp")
        assert await client.connect() is False

    asyncio.run(go())
