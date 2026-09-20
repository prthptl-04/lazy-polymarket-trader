"""What a KXBTC15M contract is worth, and what it costs to trade.

Pure arithmetic. No I/O, no clock, no config, no venue. Every function here is
checkable against hand-computed values, which is the only way to check a model:
against arithmetic, never against the market it is trying to predict.

THE CONTRACT
------------
    YES  ⟺  mean(BRTI over the 60s before close)  ≥  floor_strike

`floor_strike` is the mean of BRTI over the 60s before the OPEN, and Kalshi
publishes it on the market — it is a constant from the moment the window opens.
`strike_type` is `greater_or_equal`, so ties resolve YES.

THE ONE IDEA WORTH UNDERSTANDING
--------------------------------
The terminal is an AVERAGE over the final minute, not a point. Averaging a
diffusion shrinks its variance, so the clock runs faster than wall time:

    τ_eff = τ − 40              for τ ≥ 60
    τ_eff = τ³ / 10800          for τ < 60   (inside the averaging minute)

Both give 20 at τ = 60. Using τ instead of τ_eff overstates the variance by 40
seconds — 4.4% of the window at the open, and 200% at T−60s, which is where the
volume is. A model that makes that mistake is systematically underconfident
late, and it will overtrade exactly the part of the window it understands worst.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

# The settlement average is taken over the final minute.
AVERAGING_WINDOW_S = 60.0

# Kalshi's taker fee, from the CFTC-filed schedule, verbatim:
#
#     fees = round up(0.07 x C x P x (1-P))
#
# C is the number of contracts in the ORDER and the round-up applies once to
# the whole order, not per contract. That distinction is the whole reason this
# is a separate function: at P=0.50 one contract costs 2c (a 4% haircut on a
# 50c contract) while a hundred cost $1.75, or 1.75c each. Small clips are
# structurally expensive here, and a per-contract fee constant would have hidden
# that from the sizer completely.
FEE_COEFFICIENT = 0.07

# The maker side is flat per contract rather than quadratic. This number is NOT
# verified against the live schedule — kalshi.com rate-limited every fetch — and
# the 2022 CFTC filing says maker orders pay nothing at all, so the truth is
# somewhere in [0, 0.0025]. We take the pessimistic end: over-charging ourselves
# rejects marginal trades, under-charging books losses as wins.
#
# Confirm against the current schedule before the live flip; it is on the
# rule-#13 checklist for exactly that reason.
MAKER_FEE_PER_CONTRACT = 0.0025

# Below this the variance is numerically meaningless; the contract has effectively
# settled and the answer is an indicator, not a probability.
SETTLED_TAU_S = 1.0

# BTC realised volatility, per second. Roughly 25%–250% annualised. Outside this
# band the ESTIMATOR has broken, not the market.
SIGMA_FLOOR = 2e-5
SIGMA_CEILING = 2e-4


def tau_eff(tau_s: float) -> float:
    """Effective seconds of variance remaining, given an averaged terminal.

    Derivation, τ ≥ 60. Write the terminal average as a point plus an average:
    with a = τ − 60 and h = 60,

        (1/h)·∫_a^{a+h} W_s ds  =  W_a + (1/h)·∫_0^h B_v dv        (B ⊥ W_a)
        Var = a + h/3 = (τ − 60) + 20 = τ − 40

    Derivation, τ < 60. Only the remaining τ seconds of the averaging window are
    still random, and they enter the mean with weight (1/60) each:

        Var = σ²·(1/60²)·∫_0^τ (τ − u)² du = σ²·τ³/10800

    At τ = 60 that is 20 — continuous with the branch above, which is the check
    worth writing a test for.
    """
    if tau_s <= 0:
        return 0.0
    if tau_s >= AVERAGING_WINDOW_S:
        return tau_s - (AVERAGING_WINDOW_S * 2.0 / 3.0)
    return tau_s ** 3 / 10_800.0


def realised_weight(tau_s: float) -> float:
    """How much of the settlement average is already observed.

    Zero before the averaging minute starts; 1 at expiry. Inside the minute the
    settlement value is part history, and the history is not a forecast — it is
    known.
    """
    if tau_s >= AVERAGING_WINDOW_S:
        return 0.0
    if tau_s <= 0:
        return 1.0
    return (AVERAGING_WINDOW_S - tau_s) / AVERAGING_WINDOW_S


def model_probability(
    *,
    index: float,
    strike: float,
    sigma_per_sec: float,
    tau_s: float,
    realised_mean: Optional[float] = None,
) -> float:
    """P(YES): the chance the settlement average lands at or above the strike.

    `realised_mean` is the mean of BRTI so far within the averaging minute, and
    is required once τ < 60 — inside the minute, ignoring what has already been
    averaged in prices a bet that is partly already decided.

    Note the property that makes σ error survivable: when `index == strike` the
    numerator is exactly zero and this returns 0.5 for EVERY σ. Volatility error
    costs nothing at the money and everything away from it — which is precisely
    where the fee curve lets us trade, so σ error lands entirely on the trades we
    actually take.
    """
    if index <= 0 or strike <= 0:
        return 0.5

    if tau_s <= SETTLED_TAU_S:
        # Degenerate: the contract has settled in all but name. Return the
        # indicator rather than dividing by a variance that is numerically zero
        # and getting Φ(±1e15).
        terminal = realised_mean if realised_mean is not None else index
        return 1.0 if terminal >= strike else 0.0

    w = realised_weight(tau_s)
    if w > 0 and realised_mean is not None and realised_mean > 0:
        expected_log = w * math.log(realised_mean) + (1 - w) * math.log(index)
    else:
        expected_log = math.log(index)

    variance = (sigma_per_sec ** 2) * tau_eff(tau_s)
    if variance <= 0:
        return 1.0 if expected_log >= math.log(strike) else 0.0

    z = (expected_log - math.log(strike)) / math.sqrt(variance)
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def order_fee(price: float, contracts: int, *, maker: bool = False) -> float:
    """What Kalshi charges for ONE order of `contracts` at `price`.

        taker = ceil(0.07 * C * P * (1-P) * 100) / 100
        maker = ceil(0.0025 * C * 100) / 100

    The round-up happens once, at the order, which is why this takes a contract
    count instead of returning a per-contract rate. Symmetric in P on the taker
    side, so one branch serves both directions: fee(P) == fee(1-P).
    """
    if contracts <= 0:
        return 0.0
    p = min(max(price, 0.0), 1.0)
    if maker:
        cents = MAKER_FEE_PER_CONTRACT * contracts * 100.0
    else:
        cents = FEE_COEFFICIENT * contracts * p * (1.0 - p) * 100.0
    return math.ceil(cents - 1e-9) / 100.0


def fee_per_contract(price: float, contracts: int, *, maker: bool = False) -> float:
    """The order fee amortised over the clip, in dollars per contract.

    Takes a contract count deliberately: there is no such thing as "the" fee at
    a price. At P=0.50 it is 2c for one contract and 1.75c for a hundred, and a
    signature that let a caller forget to say how many would quietly price the
    hundred-lot as if it were a single.
    """
    if contracts <= 0:
        return 0.0
    return order_fee(price, contracts, maker=maker) / contracts


def net_edge_cents(
    *,
    model_p: float,
    contracts: int,
    yes_ask: Optional[float] = None,
    yes_bid: Optional[float] = None,
    maker: bool = False,
) -> tuple[Optional[float], Optional[float]]:
    """Edge in cents per contract, AFTER fees, for (buy YES, sell YES).

    The fee is inside the edge, not applied to the result afterwards. At P=0.20
    the taker fee is over a cent against an edge measured in cents; subtracting
    it after the decision turns a rejection into a disappointment.

    `contracts` is the clip actually being considered, because the fee is not
    linear in it. Sizing therefore has to run before this, not after.
    """
    buy = sell = None
    if yes_ask is not None and 0 < yes_ask < 1:
        fee = fee_per_contract(yes_ask, contracts, maker=maker)
        buy = (model_p - (yes_ask + fee)) * 100.0
    if yes_bid is not None and 0 < yes_bid < 1:
        fee = fee_per_contract(yes_bid, contracts, maker=maker)
        sell = ((yes_bid - fee) - model_p) * 100.0
    return buy, sell


def implied_probability(yes_bid: Optional[float], yes_ask: Optional[float]) -> Optional[float]:
    """The book's own view: the mid, or the one side that exists."""
    if yes_bid is not None and yes_ask is not None:
        return (yes_bid + yes_ask) / 2.0
    return yes_bid if yes_bid is not None else yes_ask


def sigma_per_second(
    values: Sequence[tuple[float, float]],
    *,
    spacings_s: Sequence[int] = (5, 30),
) -> Optional[float]:
    """Realised volatility of the index, per second, from its own tape.

    `values` is [(unix_seconds, index), …], oldest first.

    Three deliberate choices:

    **Zero mean, not demeaned.** Over half an hour the sample mean of BTC log
    returns is noise; subtracting it removes signal and adds estimation variance.

    **Two spacings, take the LARGER.** One-second sampling inflates σ with
    microstructure noise; 30-second sampling understates it on a smoothed index.
    Taking the larger is the conservative direction: an overstated σ pulls P
    toward 0.5 and REFUSES trades, an understated σ manufactures confidence and
    TAKES them. Refusing wrongly costs an opportunity; taking wrongly costs money.

    **A hard clamp.** Outside [2e-5, 2e-4] the estimator has broken, not BTC.
    """
    if len(values) < 2:
        return None

    best: Optional[float] = None
    for spacing in spacings_s:
        step = max(1, int(spacing))
        sampled = values[::step]
        if len(sampled) < 3:
            continue
        returns = []
        for (t0, v0), (t1, v1) in zip(sampled, sampled[1:]):
            if v0 > 0 and v1 > 0 and t1 > t0:
                returns.append(math.log(v1 / v0))
        if len(returns) < 2:
            continue
        variance = sum(r * r for r in returns) / (len(returns) - 1)
        per_second = math.sqrt(variance / step)
        best = per_second if best is None else max(best, per_second)

    if best is None:
        return None
    return min(max(best, SIGMA_FLOOR), SIGMA_CEILING)


def settlement_average(values: Sequence[tuple[float, float]], close_ts: float) -> Optional[float]:
    """The settling quantity: the mean of the index over the final 60 seconds.

    Used to resolve paper positions and — more importantly — to RECONCILE against
    Kalshi's published result. A mismatch means our index capture is broken and
    every paper result to date is void, which is worth one comparison to find out.
    """
    window = [v for t, v in values if close_ts - AVERAGING_WINDOW_S <= t < close_ts]
    if not window:
        return None
    return sum(window) / len(window)
