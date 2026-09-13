import { motion } from "framer-motion";
import { AgentRoster } from "../components/AgentRoster";
import { CostMatrix } from "../components/CostMatrix";
import { AgentScorecard } from "../components/AgentScorecard";
import { EngineButton } from "../components/EngineButton";
import { GlassCard, PanelTitle } from "../components/GlassCard";
import { EquityArea, Sparkline } from "../components/charts";
import { DrawnCheck, Empty, Pill, Stat, money, signed, toneOf } from "../components/primitives";
import {
  usePoll, type Balances, type EngineState, type Feed, type FundStatus, type Lesson,
  type PaperProgress, type Position, type Record_, type VenueStats,
} from "../lib/api";
import { useDynamicBackground } from "../lib/useDynamicBackground";

export function Overview() {
  const { data: rec } = usePoll<Record_>("/api/record");
  // Per-venue statistics, sourced from the venue itself where it has a ledger.
  const { data: polyStats } = usePoll<VenueStats>("/api/venue-stats?venue=polymarket_us", 15000);
  const { data: hoodStats } = usePoll<VenueStats>("/api/venue-stats?venue=robinhood", 15000);
  const { data: paper } = usePoll<PaperProgress>("/api/paper");
  const { data: fund } = usePoll<FundStatus>("/api/fund", 4000);
  const { data: bal } = usePoll<Balances>("/api/balances", 15000);
  const { data: engineData, refresh: refreshEngines } =
    usePoll<{ engines: Record<string, EngineState> }>("/api/engines", 6000);
  const { data: lessons } = usePoll<Lesson[]>("/api/lessons?limit=6", 12000);

  const gate = fund?.router_live_gate;

  // Trades closed before the book recorded an asset class belong to neither
  // column. They still exist, so they are counted here rather than quietly
  // dropped — a total that does not reconcile is worse than an odd label.
  const unattributed = Math.max(
    0, (rec?.closed ?? 0)
       - ((polyStats?.fund.closed ?? 0) + (hoodStats?.fund.closed ?? 0)));

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
        <PanelTitle right={
          <div className="flex items-center gap-1.5">
            {unattributed > 0 && <Pill tone="warn">{unattributed} pre-split</Pill>}
            <Pill>{fund?.session ?? "—"}</Pill>
          </div>
        }>
          Dual market · live feed
        </PanelTitle>
        {/* Each venue owns its column, stats included. One shared row of
            numbers under two charts reads as though both venues produced them;
            the divider is there so a Polymarket loss is never mistaken for the
            broker's. */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <VenuePanel
            label="Polymarket" skin="polymarket" dot="bg-poly-blue" colour="#2d52f3"
            venue="polymarket_us" engineLabel="Polymarket Engine"
            engine={engineData?.engines?.polymarket_us} onEngine={refreshEngines}
            stats={polyStats} cycles={fund?.metrics?.cycles ?? 0} />
          <VenuePanel
            label="Robinhood" skin="robinhood" dot="bg-hood-green" colour="#00c805"
            venue="robinhood" engineLabel="Robinhood Engine"
            engine={engineData?.engines?.robinhood} onEngine={refreshEngines}
            stats={hoodStats} cycles={fund?.metrics?.cycles ?? 0} />
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

      {/* ---------------- paper progress ---------------- */}
      <GlassCard className="col-span-full p-5">
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

      {/* ---------------- model spend ---------------- */}
      <CostMatrix />

      {/* ---------------- agents ---------------- */}
      <AgentScorecard />

      {/* ---------------- roster ----------------
          Where the paper panel used to sit: who to believe, ranked. */}
      <AgentRoster />

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

/**
 * One venue: its engine, its chart, and its OWN numbers.
 *
 * The headline row is the BROKER's record when that venue has a reachable
 * ledger, and the fund's paper record otherwise — a panel with a venue's name
 * on it should show that venue's money. The other record is never merged in;
 * it sits underneath, labelled, because the account's history contains trades
 * the fund never made and the fund's paper record contains fills the broker
 * never saw.
 */
function VenuePanel({ label, skin, dot, colour, venue, engineLabel, engine, onEngine, stats, cycles }: {
  label: string; skin: "polymarket" | "robinhood"; dot: string; colour: string;
  venue: string; engineLabel: string;
  engine?: EngineState; onEngine: () => void; stats?: VenueStats | null;
  cycles: number;
}) {
  const hood = skin === "robinhood";
  const fundRec = stats?.fund ?? null;
  const broker = stats?.broker;
  const live = stats?.primary === "broker" && broker?.available;

  const shown = live ? {
    realized: broker!.realized_usd ?? null,
    win_rate: broker!.win_rate ?? null,
    best: broker!.best_usd ?? null,
    worst: broker!.worst_usd ?? null,
    profit_factor: broker!.profit_factor ?? null,
    closed: broker!.closed ?? 0,
    wins: broker!.wins ?? 0,
    losses: broker!.losses ?? 0,
    invested: broker!.equity_usd ?? null,
    investedLabel: "Account equity",
  } : {
    realized: fundRec?.realized_usd ?? null,
    win_rate: fundRec?.win_rate ?? null,
    best: fundRec?.best_usd ?? null,
    worst: fundRec?.worst_usd ?? null,
    profit_factor: fundRec?.profit_factor ?? null,
    closed: fundRec?.closed ?? 0,
    wins: fundRec?.wins ?? 0,
    losses: fundRec?.losses ?? 0,
    invested: fundRec?.equity_curve?.[0] ?? null,
    investedLabel: "Total invested",
  };

  return (
    // data-venue-skin carries the venue's whole design language — ground,
    // typography, accents, controls — scoped to this column.
    // flex column so the chart takes the slack and the stats sit on the floor
    // of the panel — both venues then line up along the same bottom edge.
    <div data-venue-skin={skin} className="flex flex-col">
      <div className="flex items-center gap-2 mb-2">
        <span className={`w-2 h-2 rounded-full ${dot}`} />
        <span className="text-[12px] text-white/70">{label}</span>
        <span className="text-[10px] uppercase tracking-[0.1em] text-white/35">
          {shown.investedLabel}
        </span>
        <Pill tone={live ? "good" : "neutral"}>
          {live ? `broker · ${broker!.span ?? "all"}` : "fund paper record"}
        </Pill>
      </div>
      <EngineButton venue={venue} label={engineLabel} state={engine} onDone={onEngine} />
      {/* One size for both. Robinhood's 80px ticker is right on a page that is
          nothing but that number; in a two-up panel it just shouted over the
          other venue. */}
      <div className="font-mono text-3xl font-light tracking-tighter text-white/90 mb-1">
        {money(shown.invested)}
      </div>

      {/* The curve is always the FUND's — the broker's ledger gives realised
          totals, not a series — so it is labelled rather than passed off as
          the account's equity history. Polymarket draws it as a gradient area,
          Robinhood as a bare sparkline with no grid and no axes. */}

      <div className="text-[10px] text-white/30 mb-2 leading-relaxed">
        {live
          ? <>Figures above are {label}&rsquo;s own ledger ({broker!.trades ?? 0} trades in
              the window). Fund paper record at this venue:{" "}
              {fundRec?.closed ?? 0} closed, {signed(fundRec?.realized_usd)} realised.
              The curve is the fund&rsquo;s.</>
          : <>Fund paper record. {broker && !broker.available
              ? `${label}'s own ledger is unreachable: ${broker.reason}`
              : `${label} exposes no realised ledger to read.`}</>}
      </div>

      {/* Bled to the panel edges and stretched into whatever height is left —
          the chart is the thing worth the space. */}
      <div className="flex-1 min-h-[280px] -mx-4">
        {hood
          ? <Sparkline values={fundRec?.equity_curve ?? []} colour="#FFD700"
                       gradient={["#FFD700", "#B8860B"]} height="100%" />
          : <EquityArea values={fundRec?.equity_curve ?? []} colour={colour} height="100%" />}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-5 gap-y-4 mt-auto pt-4
                      border-t border-white/[0.07]">
        <Stat label="Realised P&L" value={signed(shown.realized)}
              tone={toneOf(shown.realized)} />
        <Stat label="Win rate"
              value={shown.win_rate == null ? "—" : `${shown.win_rate.toFixed(0)}%`}
              sub={shown.closed ? `${shown.wins}W · ${shown.losses}L` : "nothing closed here"} />
        <Stat label="Best / worst"
              value={`${signed(shown.best)} / ${signed(shown.worst)}`} />
        <Stat label="Profit factor"
              value={shown.profit_factor == null ? "—" : shown.profit_factor.toFixed(2)}
              sub="gross win ÷ gross loss" />
        {/* The scheduler runs one loop for both venues, so this number is the
            fund's, not this venue's. Labelled rather than silently duplicated. */}
        <Stat label="Cycles run" value={cycles} sub="fund-wide" />
      </div>

    </div>
  );
}

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
