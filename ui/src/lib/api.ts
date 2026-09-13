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
export function usePoll<T>(path: string, intervalMs = 5000) {
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
    void tick();
    const id = setInterval(() => void tick(), intervalMs);
    return () => { alive.current = false; clearInterval(id); };
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
  metrics?: { cycles: number; submitted: number; halted: number; errors: number };
  kill_switch?: { armed: boolean; tripped: boolean; remaining_usd: number; limit_usd: number } | null;
  pdt?: { pdt_applies: boolean; day_trades_remaining: number } | null;
  router_live_gate?: { live_possible: boolean; checks: Record<string, boolean>;
    graded_paper_trades: number; required_paper_trades: number } | null;
  last_cycle?: Record<string, unknown> | null;
}
export interface Balances {
  [venue: string]: { available: boolean; cash_usd?: number; equity_usd?: number; reason?: string };
}
export interface Record_ {
  closed: number; wins: number; losses: number; win_rate: number | null;
  realized_usd: number; best_usd: number; worst_usd: number;
  profit_factor: number | null; equity_curve: number[];
}
export interface PaperProgress {
  graded_paper_trades: number; required: number; pct_complete: number;
  deliberations: number; resolved: number; seats_calibrated: number;
  seats_scored: number; lessons_learned: number;
  seats: SeatScore[];
  shrink_fit: { usable: boolean; shrink: number | null; reason: string };
  equity_curve: number[]; realized_usd: number;
  win_rate: number | null; closed: number;
}
export interface Feed {
  symbol: string; entry: number | null; bid: number | null; ask: number | null;
  last: number | null; spread_bps: number | null; change_pct: number | null;
  reason: string | null;
}
export interface SeatScore {
  seat_id: string; seat_name: string; samples: number; hit_rate: number;
  brier: number; overconfidence: number; calibrated: boolean; beats_coin_flip: boolean;
}
export interface Agent { id: string; name: string; mandate: string; round: number; icon: string }
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
