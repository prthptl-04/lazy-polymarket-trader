from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedOutcomeCriteria:
    """Acceptance criteria the Outcome Grader checks for every proposed trade.

    Sized for the funded $500 Robinhood account (user-chosen 2026-09-20,
    superseding the $100 smoke-test defaults of 2026-05-13). Adjust before
    scaling — but never silently. Loosening these is a code review event, not
    a config tweak.
    """

    # 30% of the $500 bankroll. Chosen to let half-Kelly express itself: under
    # the fund's fixed 2xATR/3xATR geometry the payoff ratio is a constant 1.5,
    # so half-Kelly peaks at $145.83 at chair confidence 100 and this cap never
    # binds. Sizing is therefore driven by the chair's confidence, which is an
    # LLM number that is not yet calibrated — `roundtable/calibration.py` is
    # what will eventually say whether it deserves the weight.
    #
    # What actually bounds a filling book is `finance.sizing.concentration_limit`:
    # the cap governs the first three names, concentration governs the fourth
    # onward (25% = $125, then a 20% = $100 floor). That division only holds
    # because the off-by-one in `concentration_limit` was fixed first — before
    # that, position #2 could have taken the entire book at this cap.
    #
    # The previous value, $10, was correct for a $100 book and wrong from the
    # moment the account was funded: `FundLoop.run_cycle` sets
    # `pipeline.bankroll_usd = equity_usd`, so Kelly sized off the live $500
    # while the cap pinned every position at $10 — it had stopped being a cap
    # and become the size.
    max_position_usd: float = 150.0

    # NOTE: there is deliberately no `max_daily_loss_usd` here. The daily loss
    # limit lives in `config/fund.toml` and reaches `DailyLossKillSwitch` via
    # `FundConfig` — the only path that enforces it. The copy that used to sit
    # in this class had zero code readers and existed only to drift out of step
    # with the one that is real.
    min_orderbook_depth_usd: float = 500.0
    max_slippage_bps: int = 50              # 0.50%
    min_expected_edge_bps: int = 20         # 0.20%
    allowed_sides: tuple[str, ...] = ("YES", "NO")

    # ---- directional trades (equities / crypto) ----
    #
    # Prediction-market rules assume price is a probability and that an
    # orderbook depth number exists. Neither holds for "buy AAPL at $231", so
    # directional positions are graded on their own terms: the risk is not
    # bounded by the contract, it is bounded by the stop.

    allowed_directional_sides: tuple[str, ...] = ("buy", "sell")

    # The single most important rule in this file. A directional position with
    # no stop has unbounded downside, and the PDT rule means we hold it
    # overnight and cannot day-trade out of it. Entries without a stop are
    # refused outright.
    require_stop_loss: bool = True

    # Reward:risk floor. Below this the strategy needs an implausibly high hit
    # rate to break even — at 1.0R you must win >50% just to pay the spread.
    min_reward_risk_ratio: float = 1.5

    # The same ratio AFTER the round trip is paid. A separate, additional gate
    # rather than a replacement: the gross floor above still applies unchanged,
    # so nothing this constant does can loosen anything.
    #
    # Why it cannot also be 1.5. The fund's geometry is a fixed 2xATR stop and
    # 3xATR target, so the gross ratio is exactly 1.5 for every candidate — the
    # maximum achievable. Netting any cost at all puts it underneath, which
    # would refuse 100% of the fund's own candidates (measured: 2000 of 2000).
    #
    # Derivation. With risk 2A, reward 3A and round-trip cost c:
    #     (3A - c) / (2A + c) >= 1.35   =>   c <= 0.128 A
    # so the fund tolerates a round trip up to ~12.8% of one ATR. At a typical
    # ATR of 1-3% of price that is roughly 13-38 bps — tight enough to refuse a
    # crossed wide book, loose enough not to refuse an ordinary equity fill.
    #
    # Break-even moves with it: 1/(1+1.35) = 42.6%, up from 40% at 1.5. That is
    # the honest number, because the 40% never counted the cost of trading.
    min_net_reward_risk_ratio: float = 1.35

    # A stop further than this from entry is not a stop, it is a hope. Also
    # caps how much of the book one position can lose at once.
    max_stop_distance_pct: float = 0.15

    # Liquidity proxy for venues with no visible orderbook depth.
    max_spread_bps: int = 50                # regular session
    max_spread_bps_extended: int = 100      # premarket / after-hours

    # Extended-hours books are thin and gappy; demand a better expected edge
    # there than during the regular session.
    extended_hours_edge_multiplier: float = 2.0


DEFAULT_CRITERIA = VerifiedOutcomeCriteria()

# The live executor requires at least this many graded paper trades for the
# active strategy before allowing live order submission. User-chosen.
MIN_PAPER_TRADES_FOR_LIVE: int = 50

# Sentinel lesson the user must record to flip live. Stored in agent_lessons
# under agent_id='*' so every specialist sees it on their next briefing.
LIVE_APPROVAL_LESSON: str = "live trading approved"
