"""Telegram notifications for what the fund actually did.

The idea is borrowed from HOODRADAR's notification bridge; none of its code is.
That project is a research desk for Robinhood CHAIN — an EVM L2 — and its
on-chain machinery has nothing to do with a brokerage account.

What matters here is what a notifier must never do:

**Never block a cycle.** A trading loop that waits on a chat API has made
Telegram a dependency of execution. Every send is fire-and-forget off the hot
path, and a dead network loses a message rather than a trade.

**Never leak the token.** The token is a bearer credential: anyone holding it
can post as the bot. It is read from the environment, never logged, never put
in an error message, and redacted in `repr` — the same stance rule #17 takes
with the user-channel credentials.

**Never claim more than it knows.** A notification says what the venue
reported. It does not say "profit" on an entry, and it does not round a fill
price into something prettier than the fill.
"""

import pytest

from monitoring.telegram import TelegramNotifier, format_fill


class _Sent:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def __call__(self, url, payload):
        if self.fail:
            raise ConnectionError("network down")
        self.messages.append((url, payload))


# ---------- the credential ----------

def test_the_token_never_appears_in_a_repr():
    """A bearer credential in a log line is a bearer credential in a log file."""
    n = TelegramNotifier(token="123456:SUPERSECRETTOKEN", chat_id="42")
    assert "SUPERSECRETTOKEN" not in repr(n)
    assert "SUPERSECRETTOKEN" not in str(n)


def test_a_notifier_without_a_token_is_simply_off():
    """Absent configuration is not an error. Most runs will not have it."""
    n = TelegramNotifier(token=None, chat_id=None)
    assert not n.enabled
    n.notify("anything")            # must not raise


def test_a_token_without_a_chat_id_is_off():
    assert not TelegramNotifier(token="t", chat_id=None).enabled


# ---------- never break the cycle ----------

def test_a_dead_network_loses_the_message_not_the_trade():
    """A trading loop that waits on a chat API has made Telegram a dependency
    of execution."""
    n = TelegramNotifier(token="t", chat_id="1", send=_Sent(fail=True))
    n.notify("hello")               # must not raise


def test_a_message_is_sent_to_the_configured_chat():
    sent = _Sent()
    TelegramNotifier(token="tok", chat_id="99", send=sent).notify("hello")

    url, payload = sent.messages[0]
    assert "tok" in url and payload["chat_id"] == "99"
    assert payload["text"] == "hello"


# ---------- what a fill notification says ----------

def _fill(**kw):
    base = {"symbol": "AAPL", "side": "buy", "quantity": 0.5,
            "price": 334.94, "mode": "paper", "venue": "paper",
            "reason": None, "realized_usd": None}
    base.update(kw)
    return base


def test_an_entry_names_the_symbol_side_size_and_price():
    text = format_fill(_fill())
    assert "AAPL" in text and "BUY" in text
    assert "0.5" in text and "334.94" in text


def test_a_paper_fill_says_so_unmistakably():
    """A paper fill read as a real one is the single worst thing this can do."""
    assert "PAPER" in format_fill(_fill()).upper()


def test_a_live_fill_is_marked_differently():
    # mode and venue are different things, and a live-mode fill at the paper
    # venue is a contradiction — so the fixture names a real one.
    live = format_fill(_fill(mode="live", venue="robinhood"))
    assert "PAPER" not in live.upper()
    assert "LIVE" in live.upper()


def test_an_exit_reports_the_realised_result_and_why_it_closed():
    text = format_fill(_fill(side="sell", reason="stop", realized_usd=-12.40))
    assert "stop" in text.lower()
    assert "-12.40" in text or "−12.40" in text


def test_an_entry_never_claims_a_profit():
    """There is no P&L on an entry, and inventing one is how a notification
    starts lying before the position has done anything."""
    text = format_fill(_fill(realized_usd=None))
    assert "P&L" not in text and "realised" not in text.lower()


def test_the_fill_price_is_not_prettified():
    """The fill is the fill. Rounding it makes the message disagree with the
    trade ledger, and the ledger is the one that matters."""
    assert "81144.87" in format_fill(_fill(symbol="BTC-USD", price=81144.87))


# ---------- wired to the fills the fund actually books ----------

import pytest as _pytest


class _Capture:
    def __init__(self):
        self.texts = []

    enabled = True

    def notify(self, text):
        self.texts.append(text)


@_pytest.mark.asyncio
async def test_an_entry_notifies_once_it_is_actually_filled(tmp_path):
    """Filled, not submitted. An accepted resting order has not traded, and a
    notification for it would announce a position that does not exist."""
    from tests.test_fund_e2e import _stack, WEDNESDAY, BANKROLL

    loop, _, book, _ = _stack(tmp_path)
    loop.notifier = _Capture()
    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    assert book.get("AAPL") is not None
    assert any("BUY AAPL" in t for t in loop.notifier.texts)


@_pytest.mark.asyncio
async def test_an_exit_notifies_with_its_reason_and_result(tmp_path):
    from datetime import timedelta
    from tests.test_fund_e2e import _stack, _mark, WEDNESDAY, BANKROLL

    loop, venue, book, _ = _stack(tmp_path)
    loop.notifier = _Capture()
    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)
    _mark(loop, venue, book.get("AAPL").plan.stop - 1.0)
    await loop.run_cycle(WEDNESDAY + timedelta(minutes=5),
                         equity_usd=BANKROLL, available_cash_usd=BANKROLL)

    exits = [t for t in loop.notifier.texts if "SELL" in t]
    assert exits and "stop" in exits[0].lower()
    assert "realised" in exits[0].lower()


@_pytest.mark.asyncio
async def test_a_broken_notifier_never_breaks_a_cycle(tmp_path):
    """The whole point. Notification is not a dependency of execution."""
    from tests.test_fund_e2e import _stack, WEDNESDAY, BANKROLL

    class _Exploding:
        enabled = True
        def notify(self, text): raise RuntimeError("telegram is down")

    loop, _, book, _ = _stack(tmp_path)
    loop.notifier = _Exploding()
    report = await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL,
                                  available_cash_usd=BANKROLL)

    assert report.errors == []
    assert book.get("AAPL") is not None, "the trade still happened"


@_pytest.mark.asyncio
async def test_no_notifier_configured_changes_nothing(tmp_path):
    from tests.test_fund_e2e import _stack, WEDNESDAY, BANKROLL

    loop, _, book, _ = _stack(tmp_path)
    assert loop.notifier is None
    await loop.run_cycle(WEDNESDAY, equity_usd=BANKROLL, available_cash_usd=BANKROLL)
    assert book.get("AAPL") is not None
