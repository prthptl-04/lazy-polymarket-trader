# Polymarket 5-minute crypto — backend design

Status: **design, not built.** Nothing in this document is implemented.
Written 2026-09-19. Every external claim carries a source; the ones that could
not be verified are listed as open questions rather than assumed.

---

## 0. The VPN question, answered first

**We are not building VPN access, and after checking the current position, we
do not need it.** Two reasons, in order of importance.

**It is unnecessary.** Polymarket re-entered the United States legally. It
acquired the CFTC-licensed derivatives exchange QCEX, the CFTC granted an
Amended Order of Designation in November 2025, and the US venue dropped its
waitlist in **May 2026** — it is open to US residents on iOS, Android and web
with full KYC, operating as a designated contract market under federal
oversight. There is a lawful, API-accessible venue. Routing around a geo-block
to reach the offshore one buys nothing that the front door does not already
offer.

**It would put the money at risk.** The international site at polymarket.com is
**close-only for US IP addresses** — existing positions can be closed, no new
ones opened. That block exists to satisfy a regulator. Defeating it with a VPN
is a terms-of-service breach on a venue that holds our USDC, and the realistic
failure mode is not a legal argument, it is an account freeze with funds inside
it. A strategy whose profits are measured in cents per trade cannot survive one
frozen balance.

So the plan is the regulated venue, and the first task is not networking — it
is confirming the contracts we want are listed there (§2).

**One thing to check before anything is built: your state.** Federal approval is
not the whole picture. Several states — Nevada, Tennessee, Massachusetts among
them, with Ohio, Michigan, Arizona and Maryland having moved against prediction
markets on gaming-law grounds — restrict or ban these contracts, and the CFTC is
actively litigating against some of them. Availability is per-state and moving.
This is a question for the operator, not for the code.

Sources: [CFTC approval](https://www.regulatoryoversight.com/2025/12/cftc-approval-allows-polymarket-to-reenter-the-u-s-market/) ·
[PR: amended order of designation](https://www.prnewswire.com/news-releases/polymarket-receives-cftc-approval-of-amended-order-of-designation-enabling-intermediated-us-market-access-302625833.html) ·
[US status and state restrictions](https://startpolymarket.com/countries/united-states/) ·
[state-by-state](https://next.io/prediction-markets/polymarket/legal/)

---

## 1. What the contract actually is

A 5-minute crypto market asks whether an asset closes the window at or above
where it opened. Five minutes covers the whole life cycle: open, trade, resolve,
reset. Settlement is a **Chainlink oracle** print, not a venue quote. Assets
listed include BTC, ETH, XRP, SOL, DOGE and HYPE. Shares trade between 0 and 1
USDC and the price is the implied probability.

Two consequences that shape everything downstream:

1. **The settlement reference is public and external.** We can compute the same
   input the oracle will use, from the same underlying market, before the
   window closes. That is the edge — not a pricing error inside Polymarket.
2. **An "up or down" market on a 5-minute horizon is close to a coin flip**, and
   its fair value is a function of time remaining, current drift from the open,
   and realised volatility. At T+4:30 with the price 0.3% above the open, the
   true probability is far from 50% and the book may not have caught up.

Sources: [5-minute markets, mechanics](https://sailgp.com/prediction-markets/polymarket/5-minute-markets) ·
[launch and instant settlement](https://coinmarketcap.com/academy/article/polymarket-debuts-5-minute-bitcoin-prediction-markets-with-instant-settlement) ·
[the live category](https://polymarket.com/crypto/5M)

---

## 2. Open questions — resolve these before writing code

| # | Question | Why it blocks |
|---|---|---|
| Q1 | **Does the US venue list the 5-minute crypto contracts?** The 5M category is documented on polymarket.com. The US DCM lists sports, politics, culture, weather — I could not confirm 5M crypto there. | If it does not, this project stops here. There is no lawful route to those specific contracts from the US, and that is the answer, not a problem to engineer around. |
| Q2 | **Fee schedule on the US venue.** | Decides whether the strategy exists at all. See §4 — the entire edge is smaller than most fee schedules. |
| Q3 | **API access terms**: application process, whether a retail account can use programmatic access or whether it requires an ISV/FCM relationship. | Decides whether this is an API integration or a manual product. |
| Q4 | **Rate limits and WebSocket availability** on the US venue. | A 5-minute market needs sub-second quote updates; a 1 req/s REST limit makes it unplayable. |
| Q5 | **Settlement oracle detail**: which Chainlink feed, what timestamp, tie behaviour ("at or above" suggests ties resolve UP). | The tie rule is worth real money on a coin-flip contract. |

Q1 and Q2 are gating. Do not build past §7 until both are answered.

---

## 3. What "arbitrage" means here — honestly

The word is doing a lot of work in the request, and it is worth separating three
different things, because only one of them is arbitrage.

**(a) True arbitrage — real, rare, small.** If YES and NO both trade such that
`ask(YES) + ask(NO) < 1.00`, buying both pays exactly 1.00 at settlement for
less than 1.00 now. Risk-free minus fees. It exists, it is what "profits in
cents" describes, and it is the correct first strategy because it does not
require a price model. It is also the most competed: these gaps close in
milliseconds and are the one place where latency beats cleverness.

**(b) Statistical arbitrage against the oracle — the actual opportunity.** We
know the settlement input (a Chainlink print of the underlying at the window
close) and we can observe that underlying continuously on a venue with far more
liquidity than the prediction market. Fair probability at time *t*:

```
P(up) = Φ( ln(S_t / S_open) / (σ · √(T_remaining)) )
```

with σ the realised volatility of the underlying over a recent window. Trade
when the book's implied probability diverges from that by more than costs. This
is not risk-free — σ is estimated, jumps happen, and the model is wrong in the
tails — but it is where the repeatable money is.

**(c) Market making.** Quote both sides, earn the spread, manage inventory. The
highest capacity of the three and the one most likely to be already crowded.

The request describes (a) and expects it to compound. The realistic plan is (a)
as the proof of plumbing, then (b) as the strategy, with (c) only if the book
turns out to be thin enough that we are the natural liquidity.

---

## 4. The arithmetic that decides whether to build this at all

Do this calculation before writing an engine, because it can return "no".

Per round trip, the edge must exceed:

```
  spread paid (crossing, both legs)
+ venue fee (Q2)
+ settlement/withdrawal cost amortised per trade
+ adverse selection (the times the book was right and the model was wrong)
```

At a 1-cent spread on a 1.00 contract, crossing costs **100 bps**. A strategy
that earns "cents per trade" on a $1 contract earns 100–300 bps gross. The
margin is thin enough that a 2% taker fee would eliminate it outright, and thin
enough that **being a maker rather than a taker is probably mandatory**.

Concrete sizing check with the fund's current $500 bankroll: at $10 per position
and 2 cents of net edge per contract, a round trip nets roughly **$0.20**. To
make $20 in a day requires 100 clean round trips — which is 100 opportunities,
100 fills on both legs, and no adverse selection. Write that number down before
building; it is the honest expectation, and it is why §6 puts hard caps on
concurrency rather than "bet multiple times concurrently" as an unbounded goal.

---

## 5. The architectural finding: the committee cannot be in this path

**`cycle_interval_seconds = 300`. The market lives 300 seconds.** The fund's
current loop would deliberate for exactly as long as the contract exists, and
each candidate costs seven LLM calls. By the time the Chair synthesises, the
market it was reasoning about has settled.

This is not a tuning problem. CLAUDE.md #14 already states the rule — *the LLM
is NEVER in the per-tick path* — and it was written for exactly this shape of
strategy. So:

- **A separate deterministic engine** owns 5M trading. Its own loop, sub-second,
  no model calls, no round table.
- **The committee's role moves off the hot path entirely**: it reviews the
  engine's parameters between sessions, reads the post-mortems, and can halt the
  engine. It never approves an individual trade.
- **The Outcome Grader stays** — it is deterministic and fast — but needs a
  prediction-market path that understands a 5-minute horizon rather than a swing
  trade's stop and target.

Anything that tries to keep the deliberation in the loop is building a slower
version of a strategy that is already marginal.

---

## 6. Proposed architecture

```
trading/fivemin/
  discovery.py     # which windows are open, when each closes
  oracle.py        # the settlement reference: Chainlink feed + our own read
  fair_value.py    # P(up) from S_open, S_t, T_remaining, sigma  (pure, testable)
  arb.py           # (a) YES+NO < 1 scanner   (b) divergence signal
  engine.py        # the sub-second loop; owns concurrency and caps
  inventory.py     # per-window exposure, aggregate exposure, forced flatten
```

Placement under CLAUDE.md #16:

| Work | Mode | Why |
|---|---|---|
| Underlying price stream | `async` | WebSocket, event-driven |
| Polymarket book stream | `async` | WebSocket (Q4) |
| `fair_value` evaluation | `sync`, called from async | pure arithmetic, µs |
| Order submit | `async` | network, needs keepalive |
| Settlement reconciliation | `async` background task | per window, off the hot path |
| Committee review | offline | never in the loop |

**Risk controls, engine-specific** — the existing kill switch and live gate are
built for swing trades and do not bound this:

- `max_concurrent_windows` — how many 5-minute markets may be open at once.
- `max_notional_per_window` and `max_aggregate_notional`.
- **Auto-flatten at T-30s** unless the position is a matched YES+NO pair, which
  settles itself.
- **Consecutive-loss breaker**: N losing windows in a row stops the engine until
  a human restarts it. On a 5-minute cycle a broken model can take 12 positions
  an hour; the daily loss cap alone is too slow a backstop.
- The existing `DailyLossKillSwitch` still applies on top.

---

## 7. What changes in paper trading

This is where most of the work is, and where it should start — the fund has a
50-round-trip bar to clear before real money, and that bar was written for this
exact situation.

**The current paper venue cannot simulate this.** `PaperVenue` fills against a
quote with a fixed 5bps slippage. A 5-minute prediction market needs:

1. **A book, not a quote.** Fills at 0.53 when the resting size at 0.53 is 40
   shares and we want 200 is a fiction that would make every backtest profitable.
   The paper venue needs depth and partial fills.
2. **A settlement model.** The window resolves on an oracle print at a known
   timestamp — the paper engine must resolve its own positions the same way,
   from real underlying data, or the record measures nothing.
3. **Latency.** The gap between deciding and filling is where this strategy
   lives or dies. A paper venue with zero latency will show an edge that does
   not exist. Model it explicitly, with a configurable floor.

**New, and specific to this strategy:** a paper-mode metric for *fill ratio on
resting orders*. If the strategy is maker-side (§4 says it likely must be), then
"how often did our quote get hit, and was it hit only when we were wrong" is the
number that decides everything. Adverse fill rate is the adverse-selection
measurement, and nothing currently captures it.

**What carries over unchanged:** the closed-trade ledger, the R-multiple
expectancy, the bootstrap, the cost bridge (gross → cost → net), and the
graduation checklist. All of it applies; the sample just arrives far faster —
50 round trips is an afternoon, not a quarter. Which makes the statistical bar
(**n ≈ 144** for |t| > 2, per the quant review) actually reachable, and makes the
50-trade rule an even weaker gate than it already is for this strategy.

---

## 8. What changes in the Polymarket section of the UI

The venue page is built for swing trades: entry, stop, target, R:R, a position
that lives for days. None of that describes a 5-minute contract.

- **Replace Positions & exit plan** with **open windows**: symbol, window close
  countdown, side, size, entry probability, current probability, and whether the
  pair is matched (risk-free) or directional.
- **The equity curve becomes intraday.** A daily curve is the wrong resolution
  for something that trades 100 times a session.
- **New panel: fair value vs. book.** Our computed P(up) against the market's
  implied probability, per open window, with the divergence that triggered the
  trade. This is the panel that tells you whether the model or the market was
  right, and it is the one worth the most.
- **Replace R:R** with **edge in cents and realised edge after fill**, the only
  numbers that mean anything at this horizon.
- **The round table panels stay but are relabelled**: they review parameters,
  they do not approve trades. The current framing would imply the committee is
  in the loop when §5 says it must not be.

---

## 9. Funds

- The regulated venue is intermediated and KYC'd; funding is an account matter,
  not a wallet matter. No private key handling, no bridge, no `signature_type=3`
  — which is a meaningful reduction in what can go wrong.
- `trading/venues/polymarket_us.py` already exists as an adapter over the
  `polymarket-us` SDK, authenticating with an ed25519 API key pair and refusing
  to place orders unauthenticated. That is the right integration point; it needs
  a 5M-aware market discovery layer, not a rewrite.
- **Keep working capital on the venue small and sweep profits out.** A strategy
  earning cents per trade does not need a large balance, and the balance is the
  thing at risk from anything that goes wrong operationally.

---

## 10. Build order

1. **Answer Q1 and Q2.** If the 5M contracts are not listed on the US venue,
   stop — that is the finding.
2. Market discovery: enumerate open 5M windows and their close timestamps.
3. `fair_value.py` + tests. Pure, offline, no venue needed.
4. Replay harness: past windows, underlying data, what the model would have
   said. This answers "is there an edge" before any order exists.
5. Paper engine with depth, latency and oracle settlement (§7).
6. The `YES + NO < 1` scanner in paper. Simplest strategy, proves the plumbing.
7. Statistical arb in paper. Measure adverse fill rate.
8. Only then: the live gate, with 5M-specific caps and the consecutive-loss
   breaker.

Steps 3 and 4 cost nothing and can kill the project cheaply. That is the point
of doing them first.

---

## 11. What this document does not authorise

- No VPN, proxy, or residency misrepresentation (§0).
- No trading on the international venue from a restricted jurisdiction.
- No live orders until the rule-#13 checklist passes with 5M-specific caps.
- No committee involvement in a per-window decision (§5).
