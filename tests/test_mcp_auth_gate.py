"""OAuth is checked before the engine starts, not discovered nine hours later.

What this prevents, measured: the Robinhood token was issued on 12 September
with `expires_in` 738829s and lapsed the following Monday morning. The refresh
did not take, the daemon refused the browser fallback (correctly — it is a
daemon), and the fund ran on with no venue quotes until someone looked.

Nothing surfaced it because nothing checked. `expires_in` was stored without
the time it was issued, so the store could not answer "is this still good?" at
all — only "is there a string here?".
"""

import json
import time

import pytest

from trading.mcp_client import FileTokenStorage


@pytest.fixture
def store(tmp_path):
    return FileTokenStorage("https://example.test/mcp", path=tmp_path / "t.json")


class _Tokens:
    def __init__(self, **kw):
        self.access_token = kw.get("access_token", "a")
        self.refresh_token = kw.get("refresh_token", "r")
        self.expires_in = kw.get("expires_in", 3600)
        self.token_type = "Bearer"
        self.scope = "trading"
    def model_dump(self, **kw): return dict(self.__dict__)


async def _save(store, **kw):
    await store.set_tokens(_Tokens(**kw))


# ---------------------------------------------------------------- expiry

@pytest.mark.asyncio
async def test_the_store_records_when_a_token_was_issued(store):
    """Without this, `expires_in` is unusable: a lifetime with no start is not
    an expiry. It is the whole reason the lapse went unnoticed."""
    await _save(store)
    assert store.summary()["expires_at"] is not None


@pytest.mark.asyncio
async def test_a_fresh_token_is_not_expired(store):
    await _save(store, expires_in=3600)
    s = store.summary()
    assert s["expired"] is False
    assert 3500 < s["seconds_remaining"] <= 3600


@pytest.mark.asyncio
async def test_a_lapsed_token_is_reported_expired(store):
    await _save(store, expires_in=1)
    time.sleep(1.1)
    s = store.summary()
    assert s["expired"] is True
    assert s["seconds_remaining"] <= 0


@pytest.mark.asyncio
async def test_a_token_with_no_lifetime_is_unknown_not_valid(store):
    """Refusing to guess. Reporting "not expired" for a token whose lifetime we
    never learned is the same false reassurance this whole change removes."""
    await _save(store, expires_in=None)
    s = store.summary()
    assert s["expires_at"] is None and s["expired"] is None


@pytest.mark.asyncio
async def test_a_token_file_written_before_this_change_degrades(store, tmp_path):
    """Existing installs have no `obtained_at`. They must read as unknown
    rather than crash or claim validity."""
    (tmp_path / "t.json").write_text(json.dumps(
        {"https://example.test/mcp": {"tokens": {"access_token": "a",
                                                 "expires_in": 3600}}}))
    s = FileTokenStorage("https://example.test/mcp", path=tmp_path / "t.json").summary()
    assert s["authenticated"] is True
    assert s["expires_at"] is None and s["expired"] is None


@pytest.mark.asyncio
async def test_no_token_material_ever_leaves_the_summary(store):
    """A brokerage bearer token must not reach an HTTP response body."""
    await _save(store, access_token="SECRET-ACCESS", refresh_token="SECRET-REFRESH")
    blob = json.dumps(store.summary())
    assert "SECRET" not in blob


# ---------------------------------------------------------------- the gate

def _runtime(tmp_path, **kw):
    from dashboard.runtime import DashboardRuntime
    from memory.store import MemoryStore
    return DashboardRuntime(memory=MemoryStore(db_path=str(tmp_path / "r.db")), **kw)


def test_the_gate_reports_needs_auth_when_there_is_no_token(tmp_path):
    rt = _runtime(tmp_path)
    status = rt.auth_status(token_path=str(tmp_path / "absent.json"))
    assert status["needs_auth"] is True
    assert status["reason"]


def test_the_gate_reports_needs_auth_on_a_lapsed_token(tmp_path):
    import asyncio
    store = FileTokenStorage("https://agent.robinhood.com/mcp/trading",
                             path=tmp_path / "t.json")
    asyncio.run(_save(store, expires_in=1))
    time.sleep(1.1)
    status = _runtime(tmp_path).auth_status(token_path=str(tmp_path / "t.json"))
    assert status["needs_auth"] is True
    assert "expired" in status["reason"].lower()


def test_a_valid_token_does_not_need_auth(tmp_path):
    import asyncio
    store = FileTokenStorage("https://agent.robinhood.com/mcp/trading",
                             path=tmp_path / "t.json")
    asyncio.run(_save(store, expires_in=86400))
    status = _runtime(tmp_path).auth_status(token_path=str(tmp_path / "t.json"))
    assert status["needs_auth"] is False


def test_a_token_expiring_shortly_warns_without_blocking(tmp_path):
    """A token with twenty minutes left is still usable. Blocking GO on it
    would refuse a cycle the fund can complete; saying nothing is how the last
    lapse became a surprise."""
    import asyncio
    store = FileTokenStorage("https://agent.robinhood.com/mcp/trading",
                             path=tmp_path / "t.json")
    asyncio.run(_save(store, expires_in=1200))
    status = _runtime(tmp_path).auth_status(token_path=str(tmp_path / "t.json"))
    assert status["needs_auth"] is False
    assert status["expiring_soon"] is True


# ---------------------------------------------------------------- probing

def test_a_stored_token_the_server_rejects_still_needs_auth(tmp_path):
    """The gap that file inspection alone cannot close, and the exact state the
    fund was in: a token file present and well-formed, and a server that will
    not accept it. Inspecting the file says "authorised"; only connecting says
    the truth."""
    import asyncio

    class _Dead:
        async def connect(self): return False

    rt = _runtime(tmp_path, venues={"robinhood": type("V", (), {"session": _Dead()})()})
    status = asyncio.run(rt.probe_auth("robinhood",
                                       token_path=str(tmp_path / "absent.json")))
    assert status["needs_auth"] is True
    assert "reject" in status["reason"].lower() or "not" in status["reason"].lower()


def test_a_working_session_reports_ready(tmp_path):
    import asyncio

    class _Live:
        async def connect(self): return True

    rt = _runtime(tmp_path, venues={"robinhood": type("V", (), {"session": _Live()})()})
    status = asyncio.run(rt.probe_auth("robinhood"))
    assert status["needs_auth"] is False
    assert status["probed"] is True


def test_a_probe_that_hangs_does_not_hang_the_button(tmp_path):
    """GO must answer. A venue that never replies is a venue that needs
    attention, not a spinner."""
    import asyncio

    class _Hang:
        async def connect(self):
            await asyncio.sleep(30)
            return True

    rt = _runtime(tmp_path, venues={"robinhood": type("V", (), {"session": _Hang()})()})

    async def go():
        started = asyncio.get_running_loop().time()
        status = await rt.probe_auth("robinhood", probe_timeout=0.2)
        return asyncio.get_running_loop().time() - started, status

    elapsed, status = asyncio.run(go())
    assert elapsed < 3.0
    assert status["needs_auth"] is True


def test_no_venue_attached_falls_back_to_the_file(tmp_path):
    """Without a session there is nothing to probe, and refusing to start on
    that basis would block a paper run that needs no venue at all."""
    import asyncio
    rt = _runtime(tmp_path)
    status = asyncio.run(rt.probe_auth("robinhood",
                                       token_path=str(tmp_path / "absent.json")))
    assert status["probed"] is False
    assert status["needs_auth"] is True          # no token either
