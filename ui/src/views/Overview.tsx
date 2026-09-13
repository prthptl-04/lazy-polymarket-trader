import { motion } from "framer-motion";
import { AgentScorecard } from "../components/AgentScorecard";
import { EngineButton } from "../components/EngineButton";
import { GlassCard, PanelTitle } from "../components/GlassCard";
import { EquityArea } from "../components/charts";
import { DrawnCheck, Empty, Pill, Stat, money, signed, toneOf } from "../components/primitives";
import {
  usePoll, type Balances, type EngineState, type Feed, type FundStatus, type Lesson,
  type PaperProgress, type Position, type Record_,
} from "../lib/api";
import { useDynamicBackground } from "../lib/useDynamicBackground";

export function Overview() {
  const { data: rec } = usePoll<Record_>("/api/record");
  const { data: paper } = usePoll<PaperProgress>("/api/paper");
  const { data: fund } = usePoll<FundStatus>("/api/fund", 4000);
  const { data: bal } = usePoll<Balances>("/api/balances", 15000);
  const { data: engineData, refresh: refreshEngines } =
    usePoll<{ engines: Record<string, EngineState> }>("/api/engines", 6000);
  const { data: lessons } = usePoll<Lesson[]>("/api/lessons?limit=6", 12000);

  const curve = rec?.equity_curve ?? [];
  const gate = fund?.router_live_gate;

  // The ground colour tracks the open trade, bounded by its own exit plan.
  const active = useActiveTrade();
  const bg = useDynamicBackground(active?.pnl, active?.maxProfit, active?.maxLoss);

  return (
    // The tint is painted on <body> by the hook, so it carries the chrome and
    // the page margins too; this wrapper only lays the panels out.
    <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-4 gap-5 p-5">
      <ActiveTradeBanner trade={active} pct={bg.pct} />

      {/* ---------------- dual market feed ---------------- */}
      <GlassCard liquid className="col-span-full p-5">
        <PanelTitle right={<Pill>{fund?.session ?? "—"}</Pill>}>
          Dual market · live feed
        </PanelTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className="w-2 h-2 rounded-full bg-poly-blue" />
              <span className="text-[12px] text-white/70">Polymarket</span>
            </div>
            <EngineButton venue="polymarket_us" label="Polymarket Engine"
                          state={engineData?.engines?.polymarket_us} onDone={refreshEngines} />
            <EquityArea values={curve} colour="#2d52f3" height={320} />
          </div>
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className="w-2 h-2 rounded-full bg-hood-green" />
              <span className="text-[12px] text-white/70">Robinhood</span>
            </div>
            <EngineButton venue="robinhood" label="Robinhood Engine"
                          state={engineData?.engines?.robinhood} onDone={refreshEngines} />
            <EquityArea values={curve} colour="#00c805" height={320} />
          </div>
        </div>

        <div className="grid grid-cols-3 gap-x-6 gap-y-4 mt-5 pt-4 border-t border-white/[0.07]">
          <Stat label="Total invested" value={money(rec ? rec.equity_curve[0] : null)} />
          <Stat label="Realised P&L" value={signed(rec?.realized_usd)} tone={toneOf(rec?.realized_usd)} />
          <Stat label="Win rate" value={rec?.win_rate == null ? "—" : `${rec.win_rate.toFixed(0)}%`}
                sub={rec?.closed ? `${rec.wins}W · ${rec.losses}L` : "no positions closed"} />
          <Stat label="Best / worst" value={`${signed(rec?.best_usd)} / ${signed(rec?.worst_usd)}`} />
          <Stat label="Profit factor"
                value={rec?.profit_factor == null ? "—" : rec.profit_factor.toFixed(2)}
                sub="gross win ÷ gross loss" />
          <Stat label="Cycles run" value={fund?.metrics?.cycles ?? 0}
                sub={fund?.metrics?.submitted ? `${fund.metrics.submitted} orders` : "no orders yet"} />
        </div>
      </GlassCard>

      {/* ---------------- wallets ---------------- */}
      <GlassCard className="col-span-full p-6">
        <PanelTitle>Wallets</PanelTitle>
        <div className="flex flex-wrap items-end gap-x-14 gap-y-5">
          <WalletFigure label="Combined"
            value={bal ? Object.values(bal).reduce((a, v) => a + (v.available ? v.cash_usd ?? 0 : 0), 0) : null} />
          {bal && Object.entries(bal).map(([k, v]) => (
            <WalletFigure key={k}
              label={k === "polymarket_us" ? "Polymarket" : k === "robinhood" ? "Robinhood" : k}
              value={v.available ? v.cash_usd ?? 0 : null}
              note={v.available ? undefined : v.reason} />
          ))}
          <WalletFigure label="Daily loss headroom"
            value={fund?.kill_switch?.armed ? fund.kill_switch.remaining_usd : null}
            note={fund?.kill_switch && !fund.kill_switch.armed ? "kill-switch unarmed" : undefined}
            tone={fund?.kill_switch?.tripped ? "text-red-400" : undefined} />
        </div>
      </GlassCard>

      {/* ---------------- gates ----------------
          Under the wallets rather than beside the charts: it is a status strip,
          not a panel that needs a column, and taking its column back is what
          gives the graphs the width. Laid out horizontally for the same
          reason. */}
      <GlassCard className="col-span-full p-5">
        <PanelTitle right={gate
          ? <Pill tone={gate.live_possible ? "good" : "warn"}>
              {gate.graded_paper_trades} / {gate.required_paper_trades} graded
            </Pill>
          : undefined}>
          Gates · allowed actions
        </PanelTitle>
        {gate ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-x-5 gap-y-3">
              {Object.entries(gate.checks).map(([k, v]) => (
                <div key={k} className="flex items-center gap-2.5">
                  <DrawnCheck checked={v} />
                  <span className={`text-[12px] leading-tight ${v ? "text-white/75" : "text-white/40"}`}>
                    {LABELS[k] ?? k}
                  </span>
                </div>
              ))}
            </div>
            {!gate.live_possible && (
              <div className="text-[11px] text-amber-400/90 mt-4 pt-3 border-t border-white/[0.07]">
                Live trading blocked. The fund is paper-only until every box is ticked.
              </div>
            )}
          </>
        ) : <Empty title="No fund attached." hint="Set ANTHROPIC_API_KEY and a watchlist, then restart." />}
      </GlassCard>

      {/* ---------------- agents ---------------- */}
      <AgentScorecard />

      {/* ---------------- paper progress ---------------- */}
      <GlassCard className="md:col-span-2 p-5">
        <PanelTitle>Paper trading · earning the right to trade real money</PanelTitle>
        {paper && (
          <>
            <div className="flex items-baseline justify-between">
              <div className="font-mono text-[27px] font-semibold">
                {paper.graded_paper_trades}
                <span className="text-[14px] text-white/40 font-normal"> of {paper.required} graded</span>
              </div>
              <span className="text-[13px] text-white/50">{paper.pct_complete.toFixed(0)}%</span>
            </div>
            <div className="h-2 rounded-full bg-white/[0.07] mt-3 overflow-hidden">
              <motion.div className="h-full rounded-full"
                style={{ background: "linear-gradient(90deg,#2d52f3,#00c805)" }}
                initial={{ width: 0 }} animate={{ width: `${Math.max(2, paper.pct_complete)}%` }}
                transition={{ duration: 0.6, ease: "easeOut" }} />
            </div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-2.5 mt-4 text-[12px]">
              <Row k="Deliberations held" v={paper.deliberations} />
              <Row k="Theses resolved" v={paper.resolved} />
              <Row k="Lessons from losses" v={paper.lessons_learned} />
              <Row k="Confidence calibration"
                   v={paper.shrink_fit.usable ? `fitted ${paper.shrink_fit.shrink!.toFixed(2)}` : "not yet fittable"} />
            </div>
          </>
        )}
      </GlassCard>

      {/* ---------------- learning ---------------- */}
      <GlassCard className="md:col-span-1 xl:col-span-2 p-5">
        <PanelTitle>Learning from losses · improvement</PanelTitle>
        {lessons?.length ? (
          <div className="space-y-3 max-h-[260px] overflow-y-auto pr-1">
            {lessons.map((l, i) => (
              <div key={i} className="pl-3 border-l-2"
                   style={{ borderColor: l.severity === "warning" ? "#fbbf24" : "rgba(255,255,255,0.12)" }}>
                <div className="text-[11px]">
                  <span className="font-semibold text-white/80">{l.symbol}</span>{" "}
                  <span className="text-white/35">{l.code.replace(/_/g, " ")}</span>
                </div>
                <div className="text-[12px] text-white/55 mt-1 leading-relaxed">{l.lesson}</div>
              </div>
            ))}
          </div>
        ) : (
          <Empty title="Nothing learned yet."
                 hint="A lesson is written every time a position closes at a loss — who dissented and was right, whether the table was unanimous, whether the data was ever verified." />
        )}
      </GlassCard>
    </div>
  );
}

interface ActiveTrade {
  symbol: string; pnl: number; maxProfit: number; maxLoss: number; last: number;
}

/**
 * The trade the ground colour answers to.
 *
 * Live marks come from the venue feeds rather than from `/api/positions`,
 * which reports `unrealized_pct: null` until a cycle marks it — a background
 * driven by a stale entry price would sit at neutral through a whole move.
 * The bounds are the position's OWN stop and target, so "fully red" means
 * "at its stop", not "down some arbitrary dollar amount".
 */
function useActiveTrade(): ActiveTrade | undefined {
  const { data: positions } = usePoll<Position[]>("/api/positions");
  const { data: hood } = usePoll<Feed[]>("/api/feeds?venue=robinhood", 4000);
  const { data: poly } = usePoll<Feed[]>("/api/feeds?venue=polymarket_us", 4000);

  const marks = new Map<string, number>();
  for (const f of [...(hood ?? []), ...(poly ?? [])]) {
    if (f.last != null) marks.set(f.symbol, f.last);
  }
  for (const p of positions ?? []) {
    const last = marks.get(p.symbol);
    if (last == null) continue;
    // Direction is not on the wire; the plan implies it.
    const long = p.target >= p.entry;
    const dir = long ? 1 : -1;
    return {
      symbol: p.symbol,
      last,
      pnl: (last - p.entry) * p.quantity * dir,
      maxProfit: Math.abs(p.target - p.entry) * p.quantity,
      maxLoss: Math.abs(p.entry - p.stop) * p.quantity,
    };
  }
  return undefined;
}

/** Says what the colour means. A page that changes hue without explaining why
 *  is a mood ring, not an instrument. */
function ActiveTradeBanner({ trade, pct }: { trade?: ActiveTrade; pct: number }) {
  return (
    // On a veil, not on the bare tint: ink at reading weight clears 4.5:1
    // against a frosted card at every point on the ramp, and against the raw
    // saturated ground at neither end.
    <div className="col-span-full w-fit flex items-center gap-2.5 text-[11px] text-white/50
                    bg-glass-white border border-glass-border rounded-full px-3.5 py-1.5">
      <span className="w-2 h-2 rounded-full" style={{ background: "currentColor" }} />
      {trade ? (
        <span>
          Ground colour tracks <b className="text-white/80">{trade.symbol}</b>:
          {" "}{signed(trade.pnl)} unrealised, {pct > 0 ? "+" : ""}{pct.toFixed(0)}% of the way
          to its {pct >= 0 ? "target" : "stop"} (marked at {money(trade.last)}).
        </span>
      ) : (
        <span>No open position with a live mark — the ground stays neutral.</span>
      )}
    </div>
  );
}

const LABELS: Record<string, string> = {
  env_paper_trading_false: "PAPER_TRADING=false in .env",
  venue_authenticated: "Venue authenticated",
  risk_caps_live_appropriate: "Risk caps sized to bankroll",
  paper_trades_recorded: "Graded paper trades on record",
  operator_approval_lesson: "Operator approval recorded",
};

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-white/40">{k}</span>
      <span className="font-mono text-white/80">{v}</span>
    </div>
  );
}

function WalletFigure({ label, value, note, tone }: {
  label: string; value: number | null; note?: string; tone?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.09em] text-white/35">{label}</div>
      {value == null
        ? <div className="text-[15px] text-white/25 italic mt-1">{note ?? "n/a"}</div>
        : <div className={`text-4xl font-light tracking-tighter font-mono mt-0.5 ${tone ?? "text-white"}`}>
            {money(value)}
          </div>}
    </div>
  );
}
