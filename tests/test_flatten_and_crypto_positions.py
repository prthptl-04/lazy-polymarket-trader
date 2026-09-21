"""Three defects that only fire on the weekend path, all latent until now.

**B23 — the flatten books a close at the fill, and can book one at $0.00.**
`_flatten_crypto` passed `planned_price=self._fill_price(ack) or 0.0`. Two
consequences: `planned_exit == exit_price` while `exit_fill_source == "venue"`,
so the row passes `_bridge`'s filter and contributes a perfectly plausible
$0.00 of trading cost — the exact fiction that bridge exists to expose; and the
`or 0.0` fallback books `exit_price=0.0`, a **-100% realised return**, on any
filled ack the venue does not price.

**B10 — crypto positions were always empty.** `positions()` used
`_rows(data, "positions")` for both asset classes, but the crypto tool returns
`data.results`. So `_flatten_crypto` iterated an empty list, reported nothing
flattened, and the position rode into the equity session holding the capital
the handoff exists to free. Crypto rows also carry the asset at `currency.code`
and cost at `cost_bases[]`, not `symbol`/`average_buy_price`.

**B25 — `graduation()` could not show the close mix.** "50 of 50" can hide a
record that is 95% discretionary. Display-only: a blocking condition rule #13
does not name would be the gate tightening itself.
"""

from datetime import datetime

import pytest

from trading.sessions import EASTERN
from trading.venues.robinhood import RobinhoodVenue

MONDAY_EARLY = datetime(2026, 9, 21, 3, 15, tzinfo=EASTERN)


# ---------- B10: read the shape the crypto tool actually returns ----------

class _Session:
    def __init__(self, crypto_rows=None, equity_rows=None):
        self.crypto_rows = crypto_rows or []
        self.equity_rows = equity_rows or []

    def auth_summary(self):
        return {"authenticated": True}

    async def call(self, tool, args=None):
        if "accounts" in tool:
            return {"data": {"accounts": [{"account_number": "A",
                                           "rhs_account_number": "R",
                                           "agentic_allowed": True}]}}
        if "crypto_positions" in tool:
            return {"data": {"results": self.crypto_rows}}
        return {"data": {"positions": self.equity_rows}}


@pytest.mark.asyncio
async def test_a_crypto_position_is_found_under_its_own_key():
    """The crypto tool returns `results`; the equity tool returns `positions`.
    Reading only the latter meant the crypto book was invisible."""
    venue = RobinhoodVenue(session=_Session(crypto_rows=[
        {"currency": {"code": "BTC"}, "quantity": "0.5",
         "cost_bases": [{"direct_cost_basis": "40000", "direct_quantity": "0.5"}]},
    ]))
    positions = await venue.positions()

    assert [p.symbol for p in positions] == ["BTC-USD"]
    assert positions[0].asset_class == "crypto"
    assert positions[0].quantity == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_a_crypto_cost_basis_is_read_from_cost_bases():
    """`average_buy_price` does not exist on a crypto row. Reading it yields
    avg_price 0.0, which makes every realised P&L on crypto the full notional."""
    venue = RobinhoodVenue(session=_Session(crypto_rows=[
        {"currency": {"code": "ETH"}, "quantity": "2",
         "cost_bases": [{"direct_cost_basis": "5000", "direct_quantity": "2"}]},
    ]))
    assert (await venue.positions())[0].avg_price == pytest.approx(2500.0)


@pytest.mark.asyncio
async def test_equity_positions_are_unaffected():
    venue = RobinhoodVenue(session=_Session(equity_rows=[
        {"symbol": "AAPL", "quantity": "5", "average_buy_price": "100"},
    ]))
    positions = await venue.positions()
    assert [(p.symbol, p.asset_class, p.avg_price) for p in positions] == \
           [("AAPL", "equity", 100.0)]


@pytest.mark.asyncio
async def test_a_crypto_row_with_no_recognisable_symbol_is_skipped():
    venue = RobinhoodVenue(session=_Session(crypto_rows=[{"quantity": "1"}]))
    assert await venue.positions() == []


# ---------- B23: never book a close at a price nobody traded ----------

@pytest.mark.asyncio
async def test_the_flatten_prices_its_close_against_a_mark_not_the_fill(tmp_path):
    """`planned_exit == exit_price` makes the row contribute $0.00 of cost to
    `_bridge` — the exact fiction that bridge exists to expose."""
    from tests.test_fund_e2e import _stack
    from finance.exits import ExitPlan
    from trading.fund import CycleReport, Holding
    from trading.venues.base import Quote

    loop, venue, book, store = _stack(tmp_path)
    loop.data.quotes["BTC"] = Quote(symbol="BTC", bid=79_900.0, ask=80_100.0)
    venue.set_quote("BTC", bid=79_900.0, ask=80_100.0)
    book.open(symbol="BTC", asset_class="crypto", quantity=0.01,
              entry_price=79_000.0, mode="paper", venue="paper", thesis_id="t",
              entry_fill_source="venue", planned_entry=79_000.0,
              plan=ExitPlan(entry=79_000.0, stop=75_000.0, target=85_000.0,
                            direction="long", atr=1_000.0))
    await venue.place_order(__import__("trading.venues.base", fromlist=["OrderRequest"])
                            .OrderRequest(symbol="BTC", side="buy",
                                          asset_class="crypto", quantity=0.01))

    report = CycleReport(moment=MONDAY_EARLY, session="crypto_only")
    await loop._flatten_crypto([Holding(symbol="BTC", asset_class="crypto",
                                        quantity=0.01)], MONDAY_EARLY, report)

    rows = store.closed_trades()
    assert len(rows) == 1
    row = rows[0]
    assert row["exit_price"] > 0, "never book a close at zero"
    assert row["realized_return"] > -1.0, "a -100% return is a fabrication"
    assert row["planned_exit"] != row["exit_price"], (
        "planned == fill contributes $0.00 of cost to the bridge")


# ---------- B25: the close mix, display-only ----------

def test_graduation_reports_the_mix_of_exit_reasons(tmp_path):
    """'50 of 50' must not hide a record that is 95% discretionary."""
    from memory.store import MemoryStore
    from trading.live_gate import LiveTradingGate

    store = MemoryStore(db_path=str(tmp_path / "g.db"))
    for i, reason in enumerate(["stop"] * 2 + ["target"] + ["signal"] * 7):
        store.record_closed_trade({
            "symbol": "AAPL", "asset_class": "equity", "realized_usd": 1.0,
            "realized_return": 0.01, "quantity": 1, "entry_price": 100.0,
            "exit_price": 101.0, "reason": reason, "venue": "paper",
            "mode": "paper", "opened_at": 1.0, "closed_at": 3601.0,
            "held_seconds": 3600.0, "thesis_id": f"t{i}",
        })

    mix = [g for g in LiveTradingGate(memory=store, bankroll_usd=500.0)
           .graduation() if g["id"] == "close_mix"][0]

    assert "70%" in mix["detail"] and "signal" in mix["detail"]
    assert mix["ok"] is None or isinstance(mix["ok"], bool)


def test_the_close_mix_is_not_a_blocking_condition(tmp_path):
    """Rule #13 does not name it. A gate that tightens itself is not a gate."""
    from memory.store import MemoryStore
    from trading.live_gate import LiveTradingGate

    store = MemoryStore(db_path=str(tmp_path / "g2.db"))
    gate = LiveTradingGate(memory=store, bankroll_usd=500.0)
    before = gate.status()["live_possible"]
    assert any(g["id"] == "close_mix" for g in gate.graduation())
    assert gate.status()["live_possible"] == before
