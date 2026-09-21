import { useCallback, useEffect, useRef, useState } from "react";

/** Every panel reads from the real Python backend. There are no fixtures: a
 *  dashboard that renders convincing fake numbers is worse than one that shows
 *  nothing, because you cannot tell the two apart at a glance. */
export async function getJSON<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(path);
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

/**
 * Poll one endpoint. Returns the last good value, so a transient failure shows
 * stale data rather than blanking the panel — but `stale` says which it is, and
 * the UI is expected to surface that rather than quietly lying.
 */
/** How often a thing should be re-read, by what it is.
 *
 *  Named rather than sprinkled as numbers, because the right interval is a
 *  property of the DATA, not of the panel that happens to show it. A price is
 *  live; a token-cost summary is not, and polling it every second would be
 *  noise with a server bill attached.
 */
export const LIVE = 1000;    // prices, fund state, positions — the trading view
export const NEAR = 3000;    // things that change on a fill or a cycle
export const SLOW = 20000;   // scorecards, costs, lessons — minutes, not seconds

export function usePoll<T>(path: string, intervalMs = NEAR) {
  const [data, setData] = useState<T | null>(null);
  const [stale, setStale] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const alive = useRef(true);

  const tick = useCallback(async () => {
    const next = await getJSON<T>(path);
    if (!alive.current) return;
    setLoaded(true);
    if (next === null) setStale(true);
    else { setData(next); setStale(false); }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    let timer: number | undefined;

    /* SELF-SCHEDULING, not setInterval. At 1Hz an interval fires whether or
       not the previous request came back, so a slow response makes the next
       one overlap it and the queue grows until the UI is reading minutes-old
       data as fast as it can. Waiting for the answer before asking again means
       a slow endpoint degrades to a lower rate instead of to a backlog. */
    const loop = async () => {
      if (!alive.current) return;
      /* A hidden tab is nobody watching. Browsers throttle background timers
         anyway; skipping the fetch outright saves the request as well. */
      if (!document.hidden) await tick();
      if (!alive.current) return;
      timer = window.setTimeout(loop, intervalMs);
    };
    void loop();

    /* Re-reading the moment the tab comes back is the difference between a
       live view and one that looks frozen for a second on every return. */
    const onVisible = () => { if (!document.hidden) void tick(); };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      alive.current = false;
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [tick, intervalMs]);

  return { data, stale, loaded, refresh: tick };
}

export async function post(path: string) {
  try { return (await fetch(path, { method: "POST" })).ok; } catch { return false; }
}

// ---- shapes returned by the Python API ----

export interface LlmStatus {
  attached: boolean; active_provider?: string; active_model?: string;
  reason?: string; gemini_available?: boolean; failover_threshold_pct?: number;
  calls?: Record<string, number>;
  limits?: { percent_used: number | null; seconds_to_reset: number | null };
}
export interface FundStatus {
  attached: boolean; state?: string; session?: string; equities_open?: boolean;
  // Set only when the cycle loop stopped WITHOUT being asked to. A dashboard
  // that cannot tell "stopped" from "died" shows a healthy fund with a dead
  // engine — which it did, for eleven hours.
  failed_reason?: string | null;
  // Seconds until the next cycle. null when stopped.
  next_cycle_in_seconds?: number | null;
  metrics?: { cycles: number; submitted: number; halted: number; errors: number };
  kill_switch?: { armed: boolean; tripped: boolean; remaining_usd: number; limit_usd: number } | null;
  pdt?: { pdt_applies: boolean; day_trades_remaining: number } | null;
  router_live_gate?: { live_possible: boolean; checks: Record<string, boolean>;
    graded_paper_trades: number; required_paper_trades: number;
    orders?: { orders_graded: number; orders_filled: number;
               fill_rate_pct: number | null;
               fill_rate_by_session: Record<string, { graded: number; filled: number }> };
    graduation?: GraduationItem[] } | null;
  last_cycle?: Record<string, unknown> | null;
}
export interface Balances {
  [venue: string]: { available: boolean; cash_usd?: number; equity_usd?: number; reason?: string };
}
export interface Record_ {
  closed: number; wins: number; losses: number; win_rate: number | null;
  realized_usd: number; best_usd: number; worst_usd: number;
  profit_factor: number | null; profit_factor_reason?: string | null;
  equity_curve: number[];
  bridge?: Bridge; concentration?: Concentration;
}
export interface PaperProgress {
  // graded_paper_trades is CLOSED PAPER ROUND TRIPS — the rule-#13 bar, and
  // the same number LiveTradingGate enforces. graded_orders is orders that
  // passed the grader, which is a different and much larger number: an order
  // can be accepted and never trade.
  graded_paper_trades: number; required: number; pct_complete: number;
  graded_orders: number;
  deliberations: number; resolved: number; seats_calibrated: number;
  seats_scored: number; lessons_learned: number;
  seats: SeatScore[];
  shrink_fit: { usable: boolean; shrink: number | null; reason: string };
  equity_curve: number[]; realized_usd: number;
  win_rate: number | null; closed: number;
}
export interface Feed {
  // `held` false means the fund is WATCHING this, not holding it. Blurring the
  // two would let a glance read a watchlist as a portfolio.
  symbol: string; held: boolean; entry: number | null; bid: number | null; ask: number | null;
  last: number | null; spread_bps: number | null; change_pct: number | null;
  reason: string | null;
}
export interface SeatScore {
  seat_id: string; seat_name: string; samples: number; hit_rate: number;
  brier: number; overconfidence: number; calibrated: boolean; beats_coin_flip: boolean;
}
export interface Agent {
  id: string; name: string; mandate: string; round: number; icon: string;
  // Asset classes this seat has a mandate for. A seat outside it is not asked,
  // so the roster greys it rather than implying it declined to speak.
  asset_classes?: string[];
}
export interface Lesson { code: string; symbol: string | null; severity: string; lesson: string }
export interface Position {
  symbol: string; asset_class: string; quantity: number; entry: number;
  stop: number; target: number; thesis_id: string | null; unrealized_pct: number | null;
}
export interface Candles {
  symbol: string; closes: number[]; entry: number | null;
  stop: number | null; target: number | null; reason?: string;
}
export interface Deliberation {
  thesis_id: string; symbol: string; signal: string | null; confidence: number | null;
  status: string; created: number; seats: number; tally: Record<string, number>;
}

export interface TradeRow {
  thesis_id: string; symbol: string; resolved_at: number | null;
  side: string; signal: string | null; confidence: number | null;
  realized_pct: number | null; won: boolean; notes: string | null;
  blamed: { name: string; confidence: number; reasoning: string }[];
  vindicated: { name: string; signal: string; reasoning: string }[];
  abstained: string[];
  actions: { code: string; lesson: string }[];
}
export interface Opinion {
  seat_id: string; seat_name: string; signal: string; confidence: number;
  reasoning: string; key_points: string[]; concerns: string[];
  failed: boolean; error: string | null;
}
export interface LatestDebate {
  thesis_id: string; symbol: string; status: string; created: number;
  opinions: Opinion[]; unanimous: boolean; abstentions: string[];
  consensus: { signal?: string; confidence?: number; summary?: string;
               dissent?: string; transcript?: string };
}

export interface ThreadMessage {
  thesis_id: string; symbol: string; created: number | null;
  seat_id: string; seat_name: string; signal: string | null;
  confidence: number | null; reasoning: string; concerns: string[];
  failed: boolean; error: string | null; role: "seat" | "chair";
}
export interface VenueModes {
  [venue: string]: { paper: ModeState; live: ModeState };
}
export interface ModeState { on: boolean; attached: boolean }

export interface EngineState {
  adapter: string | null; attached: boolean; authenticated: boolean;
  reason: string | null; on: boolean; system_running: boolean;
  // Retired venues keep their entry so the UI can say so rather than showing
  // an empty page. See trading/venues/retired.py.
  retired?: boolean;
}
export interface AgentMatrixRow {
  // Null for the chair, which always sits. Otherwise the asset classes this
  // seat has a mandate for — outside it the seat is not asked at all.
  asset_classes?: string[] | null;
  id: string; name: string; mandate: string; round: number; icon: string;
  samples: number; abstentions: number;
  hit_rate: number | null; brier: number | null;
  mean_confidence: number | null; overconfidence: number | null;
  calibrated: boolean; beats_coin_flip: boolean;
  // How loudly this seat is counted, from its own record. 1.0 = unweighted
  // (or not yet scored); < 1 = the record is costing it votes.
  vote_weight: number;
  recent: { window: number; samples: number; hit_rate: number | null };
  prior: { samples: number; hit_rate: number | null };
  improvement_pts: number | null;
  blamed_losses: number;
  top_failure: { code: string; count: number } | null;
  failure_note: string | null;
  enforced: { applied: string[]; flagged: string[] };
  target: { required_hit_rate: number; gap: number | null; note: string };
}

export interface ModeSpend {
  calls: number; input_tokens: number; output_tokens: number;
  cache_read: number; cache_write: number;
  burned_usd: number; earned_usd: number | null; net_usd: number | null;
  cost_per_call_usd: number | null; earn_per_dollar: number | null;
}
export interface Costs {
  modes: { paper: ModeSpend; live: ModeSpend };
  total_burned_usd: number;
  pricing: { input_per_mtok: number; output_per_mtok: number; source: string;
             cache_read_multiplier: number; cache_write_multiplier: number };
  note: string | null;
}

export interface BrokerRecord {
  source: "broker"; available: boolean; reason?: string; span?: string;
  trades?: number; closed?: number; unparsed?: number;
  wins?: number; losses?: number; realized_usd?: number;
  best_usd?: number; worst_usd?: number; win_rate?: number | null;
  profit_factor?: number | null; equity_usd?: number; cash_usd?: number;
}
export interface VenueStats {
  venue: string; fund: Record_; broker: BrokerRecord | null;
  primary: "broker" | "fund";
}

export interface Edge {
  n: number; excluded: number; r_values: number[];
  mean_r: number | null; sd_r: number | null; t_stat: number | null;
  bootstrap_p5_mean_r: number | null; binomial_p: number | null;
  p0: number | null; wins: number; n_for_significance: number | null;
  verdict: string; note: string;
}
export interface SeatAgreement {
  pairs: { a: string; b: string; n: number; agreement_pct: number;
           kappa: number | null; reason: string | null; duplicate: boolean }[];
  n_deliberations: number; mean_kappa: number | null;
  mean_agreement_pct: number | null; duplicates: unknown[]; reason: string | null;
}
export interface Bridge {
  gross_usd: number | null; cost_usd: number | null; net_usd: number | null;
  trades_priced: number; trades_unpriced: number; reason: string | null;
}
export interface Concentration {
  top1_symbol: string | null; top1_share_pct: number | null;
  max_concurrent: number; reason: string | null;
}
export interface GraduationItem { id: string; label: string; ok: boolean; detail: string }

// ---- self-evolution loop (/api/evolution) -------------------------------
// The shape mirrors `DashboardRuntime.evolution`: a forward pass, the weights
// it applies, the loss it scores, and the gradient that goes back.
export interface EvoInput {
  id: string; label: string; source?: string | null;
  attached: boolean; detail: string;
}
export interface EvoStage {
  id: string; label: string; detail: string;
  inputs?: EvoInput[];
  active?: boolean; symbol?: string | null;
  answered?: number; expected?: number; closed?: number;
}
export interface EvoSeat {
  seat_id: string; seat_name: string; vote_weight: number;
  samples: number; brier: number | null; overconfidence: number | null;
  scored: boolean; min_samples: number;
  state: "answered" | "thinking" | "idle";
  signal: string | null; confidence: number | null;
}
export interface Evolution {
  forward: EvoStage[];
  weights: {
    seats: EvoSeat[];
    confidence_shrink: {
      shrink: number | null; samples: number;
      realized_hit_rate: number | null; usable: boolean; reason: string;
    };
  };
  loss: {
    closed_trades: number; reason: string;
    committee: SeatScore | null; worst_seat: SeatScore | null;
  };
  backward: {
    lessons: { text: string }[]; injecting: boolean;
    recorded: number; min_samples: number; reason: string;
    scope: { symbol: string; asset_class: string | null } | null;
  };
}

// ---- catalysts (/api/catalysts) ----------------------------------------
// Dated events, filings and insider flow, via OpenBB. `available: false`
// carries a REASON — an empty list and a gated provider mean opposite things.
export interface Catalysts {
  symbol: string;
  notes: string[];
  available: boolean;
  reason: string;
  degraded: string[];
}

// ---- provenance --------------------------------------------------------
// Where each evidence block came from and when it was true. `derived` means
// computed from this cycle's own evidence, so it has no independent age.
export interface SourceRef {
  kind: string; source: string; as_of: number | null; derived: boolean;
}
export interface Provenance {
  thesis_id?: string;
  evidence?: string | null;
  sources: SourceRef[];
}
