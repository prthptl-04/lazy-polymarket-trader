"""The kill switch.

Everything else in `trading/kalshi/` is machinery. This is the question the
machinery exists to answer: replayed over real settled windows, against the
book that was actually quoted, after the fee Kalshi actually charges — is the
edge positive?

If it is not, the correct outcome of this whole migration is a paragraph in the
docs saying so, and no live venue. That is a cheaper result than discovering it
with money, and it is why this runs before the paper engine, the dashboard and
the signing code rather than after them.

Three rules it holds to, because each one is a way backtests lie:

**No lookahead.** `Window.index_at` returns the last print at or before the
decision stamp, sigma is fitted only on history, and the quote used is the
candle *close* at a minute we had already lived through.

**The book we crossed, not the mid.** Buying lifts the ask and selling hits the
bid. A backtest that fills at the mid earns half the spread on every trade for
free, and on a market quoted a cent wide that is the entire edge.

**One trade per window.** The first signal that clears the threshold, then the
window is done. Re-entering a market we are already wrong about is how a small
loss becomes the only loss that matters.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Iterable, Optional

from trading.kalshi.fair_value import (
    implied_probability,
    model_probability,
    net_edge_cents,
    settlement_average,
    sigma_per_second,
)
from trading.kalshi.rest import KalshiPublic, Window, candle_quote

# Below this there is not enough of the window left to fit a volatility.
MIN_HISTORY_S = 120.0

# Inside this, the contract is effectively decided and the book knows it.
MIN_TAU_S = 30.0

DEFAULT_CONTRACTS = 100


@dataclass(frozen=True)
class Trade:
    ticker: str
    ts: float
    tau_s: float
    side: str                 # "yes" | "no"
    price: float              # what we paid, in dollars
    model_p: float
    book_p: float
    edge_cents: float
    contracts: int
    won: bool
    pnl_usd: float

    @property
    def pnl_per_contract_cents(self) -> float:
        return self.pnl_usd / self.contracts * 100.0


@dataclass
class ReplayResult:
    trades: list[Trade] = field(default_factory=list)
    windows: int = 0
    skipped: int = 0
    reconciled: int = 0
    mismatched: int = 0

    @property
    def net_pnl_usd(self) -> float:
        return sum(t.pnl_usd for t in self.trades)

    @property
    def mean_net_cents(self) -> Optional[float]:
        """The number the go/no-go decision is made on."""
        if not self.trades:
            return None
        return statistics.fmean(t.pnl_per_contract_cents for t in self.trades)

    @property
    def hit_rate(self) -> Optional[float]:
        if not self.trades:
            return None
        return sum(1 for t in self.trades if t.won) / len(self.trades)

    @property
    def brier(self) -> Optional[float]:
        """Is the MODEL calibrated, separately from whether it made money?

        A profitable-but-uncalibrated model is a run of luck; a calibrated one
        that loses money is a fee problem. The two failures have different
        fixes, so they get measured apart.
        """
        if not self.trades:
            return None
        return statistics.fmean(
            (t.model_p - (1.0 if t.won == (t.side == "yes") else 0.0)) ** 2
            for t in self.trades
        )

    def summary(self) -> dict:
        return {
            "windows": self.windows,
            "skipped": self.skipped,
            "trades": len(self.trades),
            "net_pnl_usd": round(self.net_pnl_usd, 2),
            "mean_net_cents": round(self.mean_net_cents, 3) if self.trades else None,
            "hit_rate": round(self.hit_rate, 4) if self.trades else None,
            "brier": round(self.brier, 4) if self.trades else None,
            "reconciled": self.reconciled,
            "mismatched": self.mismatched,
        }

    def verdict(self, *, min_trades: int = 100) -> str:
        if len(self.trades) < min_trades:
            return (f"INCONCLUSIVE — {len(self.trades)} trades, need {min_trades}. "
                    "A positive mean over a handful of windows is noise.")
        mean = self.mean_net_cents or 0.0
        if mean <= 0:
            return (f"STOP — mean net {mean:.2f}c per contract after fees over "
                    f"{len(self.trades)} trades. The edge is not there.")
        return (f"PROCEED — mean net {mean:.2f}c per contract after fees over "
                f"{len(self.trades)} trades, hit rate {self.hit_rate:.1%}.")


def replay_window(
    window: Window,
    *,
    threshold_cents: float = 1.0,
    contracts: int = DEFAULT_CONTRACTS,
    maker: bool = False,
) -> Optional[Trade]:
    """Walk one window minute by minute and take the first signal that clears.

    Returns None when nothing cleared, which is the common and correct case —
    the contract opens at even money where the fee is at its maximum, so most
    windows should produce no trade at all.
    """
    if not window.settled or window.floor_strike is None or not window.index:
        return None

    for candle in window.book:
        ts = candle.get("end_period_ts")
        if ts is None:
            continue
        ts = float(ts)
        tau = window.tau_at(ts)
        if tau < MIN_TAU_S or ts - window.open_ts < MIN_HISTORY_S or ts > window.close_ts:
            continue

        index = window.index_at(ts)
        if index is None:
            continue

        # History only — everything at or before the decision stamp.
        history = [(t, v) for t, v in window.index if t <= ts]
        sigma = sigma_per_second(history)
        if sigma is None:
            continue

        realised = (
            window.index_mean(window.close_ts - 60.0, ts)
            if tau < 60.0 else None
        )
        model_p = model_probability(
            index=index, strike=window.floor_strike,
            sigma_per_sec=sigma, tau_s=tau, realised_mean=realised,
        )

        bid, ask = candle_quote(candle)
        buy, sell = net_edge_cents(
            model_p=model_p, contracts=contracts,
            yes_ask=ask, yes_bid=bid, maker=maker,
        )
        book_p = implied_probability(bid, ask)

        won_yes = window.result == "yes"
        if buy is not None and buy >= threshold_cents and ask is not None:
            pnl = ((1.0 - ask) if won_yes else -ask) * contracts
            pnl -= _fee(ask, contracts, maker)
            return Trade(window.ticker, ts, tau, "yes", ask, model_p,
                         book_p or 0.0, buy, contracts, won_yes, pnl)
        if sell is not None and sell >= threshold_cents and bid is not None:
            no_price = 1.0 - bid
            pnl = ((1.0 - no_price) if not won_yes else -no_price) * contracts
            pnl -= _fee(no_price, contracts, maker)
            return Trade(window.ticker, ts, tau, "no", no_price, model_p,
                         book_p or 0.0, sell, contracts, not won_yes, pnl)
    return None


def replay(
    windows: Iterable[Window],
    *,
    threshold_cents: float = 1.0,
    contracts: int = DEFAULT_CONTRACTS,
    maker: bool = False,
) -> ReplayResult:
    result = ReplayResult()
    for window in windows:
        result.windows += 1
        if not window.settled or window.floor_strike is None:
            result.skipped += 1
            continue
        _reconcile(window, result)
        trade = replay_window(window, threshold_cents=threshold_cents,
                              contracts=contracts, maker=maker)
        if trade is None:
            result.skipped += 1
        else:
            result.trades.append(trade)
    return result


def _reconcile(window: Window, result: ReplayResult) -> None:
    """Does OUR settlement arithmetic reproduce Kalshi's published result?

    This is the check that makes every other number in here trustworthy. If our
    60-second mean disagrees with the exchange about who won, the index capture
    is broken and the whole replay is fiction — better to see that as a count
    than to read a P&L that was never real.
    """
    settled_at = settlement_average(list(window.index), window.close_ts)
    if settled_at is None or window.floor_strike is None:
        return
    ours = "yes" if settled_at >= window.floor_strike else "no"
    if ours == window.result:
        result.reconciled += 1
    else:
        result.mismatched += 1


def _fee(price: float, contracts: int, maker: bool) -> float:
    from trading.kalshi.fair_value import order_fee
    return order_fee(price, contracts, maker=maker)


def fetch_windows(client: KalshiPublic, *, limit: int = 200) -> list[Window]:
    """Pull settled windows with their index and book history."""
    out: list[Window] = []
    for market in client.settled_markets(limit=limit):
        window = client.window(market)
        if window is not None:
            out.append(window)
    return out


if __name__ == "__main__":  # pragma: no cover
    import json
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    client = KalshiPublic()
    print(f"fetching {n} settled windows…", file=sys.stderr)
    windows = fetch_windows(client, limit=n)
    print(f"got {len(windows)}", file=sys.stderr)

    for threshold in (0.5, 1.0, 2.0, 3.0):
        for maker in (False, True):
            r = replay(windows, threshold_cents=threshold, maker=maker)
            tag = "maker" if maker else "taker"
            print(f"threshold {threshold:>4}c {tag:<5} {json.dumps(r.summary())}")
    print()
    print(replay(windows, threshold_cents=1.0).verdict())
