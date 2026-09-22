"""The session decides the asset class, absolutely.

Caught in production by the operator at 22:10 EDT — a crypto_only session with
the committee debating META, MU, QCOM and GOOGL.

The cause was a fall-through. `_universe_for` read:

    if not session.equities_open:
        names = list(self.crypto_watchlist) or None
        if names is None and self.crypto_scout is not None:
            return <crypto scout>
        # no crypto scout attached -> names is still None, and control
        # continues past the elif chain...
    ...
    return <EQUITY scout>

So with an empty crypto watchlist and no crypto scout, a crypto session ran the
equity screen. And the crypto scout was never attached in paper mode:
`build_crypto_scout` needs `currency_pairs`, which lives on the Robinhood
adapter and not on the PaperVenue that stands in for it.

Two defects, and the ordering matters. Attaching the scout fixes today's
symptom; only the boundary fixes the class of bug, because any future path that
leaves the crypto branch without returning would fall through again. A session
boundary must hold whatever is or is not attached.
"""

import pytest

from trading.fund import FundLoop
from trading.sessions import Session


class _Scout:
    def __init__(self, symbols): self.symbols = symbols
    def scan(self, **kw):
        return [type("C", (), {"symbol": s})() for s in self.symbols]


class _Boom:
    def scan(self, **kw): raise RuntimeError("provider down")


def _fund(**kw):
    f = FundLoop.__new__(FundLoop)
    f.crypto_watchlist = kw.get("crypto_watchlist", ())
    f.equity_watchlist = kw.get("equity_watchlist", ())
    f.scout = kw.get("scout")
    f.crypto_scout = kw.get("crypto_scout")
    f.max_candidates_per_cycle = 5
    f._cooling_off = {}
    return f


def _crypto_session():
    return next(s for s in Session if not s.equities_open)


def _equity_session():
    return next(s for s in Session if s.equities_open)


def _universe(fund, session, moment):
    return fund._universe_for(session, moment)


@pytest.fixture
def moment():
    from datetime import datetime
    from trading.sessions import EASTERN
    # A Tuesday night: crypto session, and outside the weekend handoff window.
    return datetime(2026, 9, 22, 22, 10, tzinfo=EASTERN)


# ---------------------------------------------------------------- the boundary

def test_a_crypto_session_never_runs_the_equity_screen(moment):
    """The production bug, stated directly. No crypto scout attached, and the
    equity scout is full of equities — the answer must still be nothing."""
    fund = _fund(scout=_Scout(["META", "MU", "QCOM"]), crypto_scout=None)
    assert _universe(fund, _crypto_session(), moment) == []


def test_a_crypto_session_with_a_failing_crypto_scout_does_not_fall_back(moment):
    """A provider outage must not silently change asset class."""
    fund = _fund(scout=_Scout(["META", "MU"]), crypto_scout=_Boom())
    assert _universe(fund, _crypto_session(), moment) == []


def test_an_equity_watchlist_is_ignored_during_a_crypto_session(moment):
    fund = _fund(equity_watchlist=("AAPL", "MSFT"), scout=_Scout(["META"]))
    assert _universe(fund, _crypto_session(), moment) == []


def test_a_crypto_session_uses_the_crypto_scout_when_it_has_one(moment):
    fund = _fund(crypto_scout=_Scout(["NEAR-USD", "AVAX-USD"]),
                 scout=_Scout(["META"]))
    assert _universe(fund, _crypto_session(), moment) == ["NEAR-USD", "AVAX-USD"]


def test_a_crypto_watchlist_still_overrides_the_crypto_scout(moment):
    fund = _fund(crypto_watchlist=("BTC", "ETH"),
                 crypto_scout=_Scout(["NEAR-USD"]), scout=_Scout(["META"]))
    assert _universe(fund, _crypto_session(), moment) == ["BTC", "ETH"]


def test_every_symbol_a_crypto_session_returns_classifies_as_crypto(moment):
    """The end-to-end invariant. If this ever fails, the fund is about to ask
    SEC EDGAR about a token again."""
    from trading.fund import classify_asset_class
    fund = _fund(crypto_scout=_Scout(["NEAR-USD", "AVAX-USD", "ZEC-USD"]))
    for sym in _universe(fund, _crypto_session(), moment):
        assert classify_asset_class(sym) == "crypto", sym


# ---------------------------------------------------------------- the other side

def test_an_equity_session_still_uses_the_equity_scout():
    from datetime import datetime
    from trading.sessions import EASTERN
    noon = datetime(2026, 9, 22, 12, 0, tzinfo=EASTERN)
    fund = _fund(scout=_Scout(["META", "MU"]), crypto_scout=_Scout(["NEAR-USD"]))
    assert _universe(fund, _equity_session(), noon) == ["META", "MU"]


def test_an_equity_session_never_returns_a_crypto_pair():
    from datetime import datetime
    from trading.sessions import EASTERN
    from trading.fund import classify_asset_class
    noon = datetime(2026, 9, 22, 12, 0, tzinfo=EASTERN)
    fund = _fund(scout=_Scout(["META", "MU"]), crypto_scout=_Scout(["NEAR-USD"]))
    for sym in _universe(fund, _equity_session(), noon):
        assert classify_asset_class(sym) == "equity", sym
