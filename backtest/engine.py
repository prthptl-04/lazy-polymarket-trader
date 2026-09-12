"""Backtest engine — replays history through the real trading path.

The design decision that matters: this does NOT simulate its own fills or its
own rules. It drives `VenueRouter` and `PaperVenue`, the same objects live
trading uses. So a backtest automatically inherits the PDT gate, the session
calendar, and the extended-hours restrictions — and a strategy that looks
profitable here is one that was actually *allowed* to place those trades.

That matters more than it sounds. A sub-$25k account cannot take four day
trades in five business days. A backtest that ignores that constraint will
report returns the account is legally incapable of earning, which is the most
expensive kind of wrong answer.

Three ways backtests lie, and what is done about each:

1. **Look-ahead.** `BarContext` exposes only bars up to and including the
   current index. The future is not reachable from the strategy — not by
   convention, by construction.
2. **Fill timing.** A signal formed on bar i's close fills at bar i+1's open.
   Filling at the close of the bar you decided on is free money that does not
   exist.
3. **Costs.** Fills cross the spread and pay slippage via PaperVenue. Commission
   is configurable and defaults to zero, which is right for Robinhood equities
   but wrong for most other venues.

Not modelled, and worth stating plainly: survivorship bias (feed it delisted
names too), partial fills, queue position, borrow cost on shorts, and dividends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Sequence

from finance.risk_metrics import (
    conditional_value_at_risk,
    max_drawdown,
    sharpe_ratio,
    value_at_risk,
)
from trading.pdt import DayTradeTracker
from trading.sessions import session_at
from trading.venues.base import AssetClass, OrderRequest
from trading.venues.paper import PaperVenue
from trading.venues.router import VenueRouter


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def dollar_volume(self) -> float:
        return self.close * self.volume


@dataclass(frozen=True)
class BarContext:
    """What a strategy may see at one point in time.

    History is sliced to [0..index]; there is no accessor for later bars. A
    strategy physically cannot peek ahead.
    """

    symbol: str
    index: int
    history: Sequence[Candle]
    equity_usd: float
    cash_usd: float
    position_quantity: float
    session: str
    pdt_day_trades_remaining: int

    @property
    def now(self) -> datetime:
        return self.history[-1].timestamp

    @property
    def current(self) -> Candle:
        return self.history[-1]

    @property
    def closes(self) -> list[float]:
        return [c.close for c in self.history]

    @property
    def returns(self) -> list[float]:
        closes = self.closes
        return [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
            if closes[i - 1]
        ]


# A strategy sees one bar of context and returns orders (possibly none).
Strategy = Callable[[BarContext], "Sequence[OrderRequest] | None"]


@dataclass
class BacktestConfig:
    symbol: str
    asset_class: AssetClass = "equity"
    starting_cash_usd: float = 10_000.0
    spread_bps: int = 5
    slippage_bps: int = 5
    commission_per_order_usd: float = 0.0
    warmup_bars: int = 20
    account_equity_for_pdt_usd: Optional[float] = None


@dataclass
class BacktestResult:
    config: BacktestConfig
    equity_curve: list[float] = field(default_factory=list)
    timestamps: list[datetime] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    rejections: list[dict] = field(default_factory=list)
    final_equity_usd: float = 0.0

    # ---------- performance ----------

    @property
    def returns(self) -> list[float]:
        c = self.equity_curve
        return [
            (c[i] - c[i - 1]) / c[i - 1]
            for i in range(1, len(c))
            if c[i - 1]
        ]

    @property
    def total_return_pct(self) -> float:
        if not self.equity_curve or not self.equity_curve[0]:
            return 0.0
        return (self.equity_curve[-1] / self.equity_curve[0] - 1.0) * 100

    def metrics(self) -> dict:
        r = self.returns
        rejected_by_gate: dict[str, int] = {}
        for rej in self.rejections:
            gate = rej.get("gate") or "venue"
            rejected_by_gate[gate] = rejected_by_gate.get(gate, 0) + 1
        return {
            "total_return_pct": round(self.total_return_pct, 4),
            "final_equity_usd": round(self.final_equity_usd, 2),
            "sharpe": round(sharpe_ratio(r), 4),
            "max_drawdown_pct": round(max_drawdown(self.equity_curve) * 100, 4),
            "var_95_pct": round(value_at_risk(r) * 100, 4),
            "cvar_95_pct": round(conditional_value_at_risk(r) * 100, 4),
            "fills": len(self.fills),
            "rejections": len(self.rejections),
            "rejected_by_gate": rejected_by_gate,
            "bars": len(self.equity_curve),
        }


class Backtester:
    """Replays candles through the live trading path."""

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.venue = PaperVenue(
            name="backtest",
            starting_cash_usd=config.starting_cash_usd,
            slippage_bps=config.slippage_bps,
            supported=(config.asset_class,),
        )
        equity_for_pdt = (
            config.account_equity_for_pdt_usd
            if config.account_equity_for_pdt_usd is not None
            else config.starting_cash_usd
        )
        self.pdt = DayTradeTracker(account_equity_usd=equity_for_pdt)
        self.router = VenueRouter(adapters=[self.venue], pdt=self.pdt)

    async def run(self, candles: Sequence[Candle], strategy: Strategy) -> BacktestResult:
        cfg = self.config
        result = BacktestResult(config=cfg)
        if len(candles) < 2:
            return result

        pending: list[OrderRequest] = []

        for i, candle in enumerate(candles):
            # 1. Fill anything decided on the previous bar, at THIS bar's open.
            #    Quoting off the open is what makes the delay real.
            self._quote(cfg.symbol, candle.open)
            for order in pending:
                await self._submit(order, candle.timestamp, result)
            pending = []

            # 2. Mark the book at this bar's close.
            self._quote(cfg.symbol, candle.close)
            equity = (await self.venue.account()).equity_usd
            result.equity_curve.append(equity)
            result.timestamps.append(candle.timestamp)

            # 3. Ask the strategy for orders — it sees history only up to here.
            if i < cfg.warmup_bars or i == len(candles) - 1:
                # No warmup data, or no next bar to fill against.
                continue

            position = next(
                (p.quantity for p in await self.venue.positions()
                 if p.symbol == cfg.symbol),
                0.0,
            )
            ctx = BarContext(
                symbol=cfg.symbol,
                index=i,
                history=candles[: i + 1],
                equity_usd=equity,
                cash_usd=self.venue.cash_usd,
                position_quantity=position,
                session=session_at(candle.timestamp).value,
                pdt_day_trades_remaining=self.pdt.status(candle.timestamp)[
                    "day_trades_remaining"
                ],
            )
            orders = strategy(ctx) or []
            pending = list(orders)

        result.final_equity_usd = result.equity_curve[-1] if result.equity_curve else 0.0
        return result

    # ---------- internals ----------

    def _quote(self, symbol: str, price: float) -> None:
        """Synthesise a two-sided quote around a single traded price."""
        half = price * (self.config.spread_bps / 10_000.0) / 2.0
        self.venue.set_quote(symbol, bid=price - half, ask=price + half, last=price)

    async def _submit(
        self, order: OrderRequest, moment: datetime, result: BacktestResult
    ) -> None:
        quote = await self.venue.get_quote(order.symbol)
        ack = await self.router.place(order, moment, quote=quote)
        if ack.accepted and ack.raw:
            if self.config.commission_per_order_usd:
                self.venue.cash_usd -= self.config.commission_per_order_usd
            result.fills.append({
                "timestamp": moment,
                "symbol": order.symbol,
                "side": order.side,
                "quantity": ack.raw.get("quantity"),
                "price": ack.raw.get("fill_price"),
                "thesis_id": order.thesis_id,
            })
        elif not ack.accepted:
            result.rejections.append({
                "timestamp": moment,
                "symbol": order.symbol,
                "side": order.side,
                "error": ack.error,
                "gate": _gate_of(ack.error),
            })


def _gate_of(error: Optional[str]) -> Optional[str]:
    """Router rejections are tagged '[gate] reason'; venue ones are not."""
    if not error or not error.startswith("["):
        return None
    end = error.find("]")
    return error[1:end] if end > 0 else None
