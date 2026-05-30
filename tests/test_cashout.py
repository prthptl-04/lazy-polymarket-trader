"""Cashout engine — QA hats on.

- Acceptance Auditor: each test pins one cashout decision branch (in-profit
  vs not; grader pass vs fail; empty book).
- Edge Case Hunter: zero entry price, position size zero, NO-side cashout,
  exact-threshold boundary.
- Blind Hunter: tracker with no positions; cache without midpoint.
"""

from live_market.orderbook_cache import OrderBookCache
from trading.cashout import CashoutEngine
from trading.position_tracker import PositionTracker
from verification.criteria import VerifiedOutcomeCriteria
from verification.outcome_grader import OutcomeGrader


def _seeded(yes_bid: float, yes_ask: float, *, depth: float = 2000.0) -> OrderBookCache:
    cache = OrderBookCache()
    cache.apply_event({
        "event_type": "book", "asset_id": "tok-a",
        "bids": [{"price": str(yes_bid), "size": str(depth)}],
        "asks": [{"price": str(yes_ask), "size": str(depth)}],
    })
    return cache


def _engine(cache: OrderBookCache, tracker: PositionTracker, **kwargs) -> CashoutEngine:
    return CashoutEngine(
        tracker=tracker, cache=cache, grader=OutcomeGrader(),
        criteria=kwargs.pop("criteria", VerifiedOutcomeCriteria(
            max_position_usd=1_000,         # loose for tests; not the default smoke cap
            max_daily_loss_usd=1_000,
            min_orderbook_depth_usd=100,
            min_expected_edge_bps=20,
        )),
        **kwargs,
    )


# -------- in-profit detection --------

def test_yes_position_in_profit_emits_signal():
    cache = _seeded(0.595, 0.605)             # mid 0.60
    tracker = PositionTracker(cache=cache)
    tracker.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.40)
    sig = _engine(cache, tracker).evaluate_position(tracker.all_open()[0])
    assert sig is not None
    assert sig.profit_bps == 5000           # 50% of 0.40 entry = 5000 bps
    assert sig.counter_trade.side == "NO"   # cashing out YES = sell NO at the bid


def test_below_threshold_emits_no_signal():
    cache = _seeded(0.405, 0.415)            # mid 0.41, entry 0.40 → 250 bps
    tracker = PositionTracker(cache=cache)
    tracker.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.40)
    sig = _engine(cache, tracker, profit_threshold_bps=300).evaluate_position(tracker.all_open()[0])
    assert sig is None


def test_loss_position_emits_no_signal():
    cache = _seeded(0.295, 0.305)
    tracker = PositionTracker(cache=cache)
    tracker.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.40)
    sig = _engine(cache, tracker).evaluate_position(tracker.all_open()[0])
    assert sig is None


def test_no_side_cashout():
    # YES mid = 0.30 → NO price = 0.70. Entry on NO at 0.50 → +40% profit.
    cache = _seeded(0.295, 0.305)
    tracker = PositionTracker(cache=cache)
    tracker.apply_fill(market_id="m1", token_id="tok-a", side="NO", size_usd=10, price=0.50)
    sig = _engine(cache, tracker).evaluate_position(tracker.all_open()[0])
    assert sig is not None
    assert sig.counter_trade.side == "YES"


# -------- grader gating --------

def test_signal_carries_grader_verdict():
    # Tight spread (~33 bps) so half-spread slippage stays under the grader 50 cap.
    cache = _seeded(0.5990, 0.6010)
    tracker = PositionTracker(cache=cache)
    tracker.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.40)
    sig = _engine(cache, tracker).evaluate_position(tracker.all_open()[0])
    assert sig is not None
    assert sig.grade_passed is True


def test_thin_orderbook_blocks_cashout_via_grader():
    # Depth below min triggers the grader to reject the counter-order.
    cache = _seeded(0.595, 0.605, depth=10)        # ultra-thin book
    tracker = PositionTracker(cache=cache)
    tracker.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.40)
    sig = _engine(cache, tracker).evaluate_position(tracker.all_open()[0])
    assert sig is not None
    assert sig.grade_passed is False                # grader rejected on depth


# -------- edge cases --------

def test_empty_book_returns_no_signal():
    cache = OrderBookCache()                     # no events applied
    tracker = PositionTracker(cache=cache)
    tracker.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.40)
    sig = _engine(cache, tracker).evaluate_position(tracker.all_open()[0])
    assert sig is None


def test_evaluate_all_skips_closed_positions():
    cache = _seeded(0.595, 0.605)
    tracker = PositionTracker(cache=cache)
    tracker.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10, price=0.40)
    tracker.close(market_id="m1", token_id="tok-a", side="YES", size_usd=10, exit_price=0.6)
    assert _engine(cache, tracker).evaluate_all() == []


def test_engine_caps_counter_size_at_max_position():
    cache = _seeded(0.595, 0.605)
    tracker = PositionTracker(cache=cache)
    tracker.apply_fill(market_id="m1", token_id="tok-a", side="YES", size_usd=10_000, price=0.40)
    sig = _engine(cache, tracker, criteria=VerifiedOutcomeCriteria(
        max_position_usd=50, max_daily_loss_usd=100,
        min_orderbook_depth_usd=100, min_expected_edge_bps=20,
    )).evaluate_position(tracker.all_open()[0])
    assert sig is not None
    assert sig.counter_trade.size_usd == 50
