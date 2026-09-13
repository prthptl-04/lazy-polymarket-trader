import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Preflight, type Step } from "../components/Preflight";
import { GlassCard, PanelTitle } from "../components/GlassCard";
import { AgentDialogue } from "../components/AgentDialogue";
import { LiveFeed } from "../components/LiveFeed";
import { MarketStance } from "../components/MarketStance";
import { RoundTableFeed } from "../components/RoundTableFeed";
import { RoundTableThread } from "../components/RoundTableThread";
import { TradeHistory } from "../components/TradeHistory";
import { EquityArea, Sparkline, type Marker } from "../components/charts";
import { DrawnCheck, Empty, Pill, Stat, money, signed, toneOf } from "../components/primitives";
import { AgentRoundTable } from "../components/AgentRoundTable";
import { usePolymarketTheme } from "../lib/polymarketTheme";
import { RH_GOLD as GOLD, RH_GOLD_DEEP as GOLD_DEEP, useRobinhoodTheme } from "../lib/robinhoodTheme";
import {
  post, usePoll, type Balances, type EngineState, type FundStatus, type ModeState,
  type PaperProgress, type Position, type Record_, type VenueModes,
} from "../lib/api";

type Venue = "polymarket_us" | "robinhood";

/**
 * One component serves both venue pages. They differ in palette and chart type
 * (Polymarket: blue gradient area; Robinhood: stark neon sparkline), not in
 * structure — and duplicating ~300 lines to vary two constants would be a
 * maintenance tax with no payoff.
 *
 * The page is split down a hard vertical line for one reason: **left is real,
 * right is simulated.** Live feeds, real positions and the trade record sit on
 * the left; everything the paper engine produces sits on the right. Mixing a
 * simulated P&L into a column of live numbers is how a paper result gets read
 * as a real one, so the divider is load-bearing, not decoration.
 */
export function VenueView({ venue }: { venue: Venue }) {
  const poly = venue === "polymarket_us";
  // Repaints the document while this tab is open; restored on unmount.
  usePolymarketTheme(poly);
  useRobinhoodTheme(!poly);
  const colour = poly ? "#2d52f3" : "#00c805";
  const title = poly ? "Polymarket" : "Robinhood";

  const { data: fund } = usePoll<FundStatus>("/api/fund", 4000);
  const { data: bal } = usePoll<Balances>("/api/balances", 15000);
  const { data: rec } = usePoll<Record_>("/api/record");
  const { data: paper } = usePoll<PaperProgress>("/api/paper", 10000);
  const { data: positions } = usePoll<Position[]>("/api/positions");
  const { data: modeData, refresh: refreshModes } =
    usePoll<{ modes: VenueModes }>("/api/venue-modes", 8000);
  const { data: engineData } = usePoll<{ engines: Record<string, EngineState> }>("/api/engines", 6000);
  const engine = engineData?.engines?.[venue];

  const wallet = bal?.[venue];
  // Modes are keyed by ADAPTER name. The venue key exists in that map even when
  // no adapter is registered under it — execution currently sits on the shared
  // paper adapter — so the key is chosen by whether an adapter is actually
  // attached, not by whether the entry exists. Picking the empty entry would
  // disable a switch that has something to drive.
  const modes = modeData?.modes ?? {};
  const wired = (k?: string) =>
    !!k && (!!modes[k]?.paper?.attached || !!modes[k]?.live?.attached);
  const modeKey = wired(venue) ? venue : Object.keys(modes).find(wired) ?? venue;
  const liveMode: ModeState | undefined = modeKey ? modes[modeKey]?.live : undefined;
  const paperMode: ModeState | undefined = modeKey ? modes[modeKey]?.paper : undefined;
  const [preflight, setPreflight] = useState<null | "paper" | "live">(null);
  const systemOn = fund?.state === "running" || fund?.state === "starting";

  // The checks the toast walks through. They are READ from live state rather
  // than assumed, so a step that says "on" means the API said so a moment ago.
  const steps: Step[] = preflight === "live" ? [
    { label: "Project धन is running", ok: systemOn,
      detail: systemOn ? undefined : "start the system from the top-right control" },
    { label: `${title} engine is connected`, ok: !!engine?.on,
      detail: engine?.on ? engine.adapter ?? undefined
                         : engine?.reason ?? "start the engine from the Overview" },
    { label: "Rule-#13 live gate allows live orders",
      ok: !!fund?.router_live_gate?.live_possible,
      detail: fund?.router_live_gate?.live_possible ? undefined
        : `${fund?.router_live_gate?.graded_paper_trades ?? 0}/${fund?.router_live_gate?.required_paper_trades ?? 50} graded paper trades, and the checklist is not complete` },
    { label: "Seats begin acting on live data", ok: true,
      detail: "orders route to the live venue; exits still route to whoever holds the position" },
  ] : [
    { label: "Project धन is running", ok: systemOn,
      detail: systemOn ? undefined : "start the system from the top-right control" },
    { label: `${title} engine is off`, ok: true, required: false,
      detail: engine?.on ? "engine is connected — its quotes are real, fills stay simulated"
                         : "not needed: paper fills are simulated either way" },
    { label: "Seats begin paper trading", ok: true,
      detail: "every fill is simulated; the record counts toward the rule-#13 bar" },
  ];

  const setMode = async (mode: "paper" | "live", start: boolean) => {
    if (!modeKey) return;
    await post(`/api/venue-sessions/${modeKey}/${mode}/${start ? "start" : "stop"}`);
    void refreshModes();
  };
  const curve = rec?.equity_curve ?? [];
  const paperCurve = paper?.equity_curve ?? [];
  const mine = (positions ?? []).filter((p) =>
    poly ? p.asset_class === "prediction" : p.asset_class !== "prediction");

  return (
    <div className="p-5 space-y-5">
      <AnimatePresence>
        {preflight && (
          <Preflight
            key={preflight}
            title={`${title} · ${preflight === "live" ? "going live" : "paper trading"}`}
            steps={steps}
            onComplete={() => void setMode(preflight, true)}
            onDismiss={() => setPreflight(null)} />
        )}
      </AnimatePresence>
      {/* ---------- §1 execution, full width above the split ---------- */}
      <GlassCard liquid className="p-5">
        <div className="flex flex-col lg:flex-row lg:items-center gap-5">
          <div className="flex items-center gap-2.5 lg:w-[230px]">
            <span className="w-2.5 h-2.5 rounded-full" style={{ background: colour }} />
            <h1 className="text-[13px] font-semibold uppercase tracking-[0.16em] text-white/70">
              {title} · Execution
            </h1>
          </div>

          {/* Scheduler state and market session read horizontally beside the
              switch — the switch gates THIS venue, the scheduler runs the
              cycle, and conflating the two is how you stop a venue and think
              you stopped the fund. */}
          <div className="flex items-center gap-6 lg:ml-2">
            <Readout label="Scheduler" value={fund?.state ?? "stopped"}
                     tone={fund?.state === "running" ? "text-hood-green" : "text-white/45"} />
            <Readout label="Session" value={fund?.session ?? "—"}
                     tone={fund?.equities_open ? "text-hood-green" : "text-white/45"} />
            <Readout label="Cycles" value={String(fund?.metrics?.cycles ?? 0)} />
          </div>

        </div>
        <div className="text-[11px] text-white/35 mt-3 lg:pl-[242px]">
          {title} engine: <b className={engine?.on ? "text-hood-green" : "text-white/50"}>
            {engine?.on ? "connected" : "off"}</b>. Start it from the Overview.
          Go&nbsp;Live and paper both refuse to open positions with the engine down,
          and neither bypasses a gate — the grader and the rule-#13 live gate run
          on every order.
        </div>
      </GlassCard>

      {/* Live takes three quarters, paper one: the live column carries the
          account, the feed, the plan and the whole trade record, and the paper
          column is a status rail. Splitting them evenly gave the simulation the
          same visual weight as the money. */}
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-0">
        {/* ================= LEFT — live (75%) ================= */}
        <div className="xl:col-span-3 space-y-5 xl:pr-6">
          <div className="flex items-center gap-3 px-1">
            <span className="text-[10px] uppercase tracking-[0.16em] text-white/45 font-semibold">
              Live
            </span>
            <ModeToggle mode="live" label="Go Live" state={liveMode} venue={modeKey}
                        gateOpen={!!fund?.router_live_gate?.live_possible}
                        onSet={(v) => (v ? setPreflight("live") : setMode("live", false))} />
            <span className="h-px flex-1 bg-white/[0.09]" />
          </div>

          <GlassCard liquid className="p-5">
            <div className="flex items-start justify-between mb-3">
              <div>
                <div className="text-[12px] text-white/55">{title} account</div>
                {wallet?.available ? (
                  <div className="venue-figure gold-text text-5xl font-light tracking-tighter font-mono mt-1">
                    {money(wallet.equity_usd)}
                  </div>
                ) : (
                  <div className="mt-2 max-w-sm">
                    <div className="text-2xl font-light text-white/30">—</div>
                    <div className="text-[11px] text-white/35 mt-1 leading-relaxed">
                      {wallet?.reason ?? "unavailable"}
                    </div>
                  </div>
                )}
                {rec && (
                  <div className={`text-[13px] mt-1 ${toneOf(rec.realized_usd)}`}>
                    {signed(rec.realized_usd)} realised
                  </div>
                )}
              </div>
            </div>

            {poly
              ? <EquityArea values={curve} colour={colour} markers={markersFrom(mine, curve)} height={230} />
              : <Sparkline values={curve} colour={GOLD} gradient={[GOLD, GOLD_DEEP]}
                           markers={markersFrom(mine, curve)} />}

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-5 pt-4 border-t border-white/[0.07]">
              <Stat label="Positions" value={mine.length} />
              <Stat label="Open risk"
                    value={money(mine.reduce((a, p) => a + Math.abs(p.entry - p.stop) * p.quantity, 0))}
                    sub="max loss if all stops hit" />
              <Stat label="Buying power" value={wallet?.available ? money(wallet.cash_usd) : "—"} />
              <Stat label="Cycles" value={fund?.metrics?.cycles ?? 0} />
            </div>
          </GlassCard>

          <LiveFeed venue={venue} title={`${title} live data feed`} />

          <RoundTableThread title={`${title} live round table · discussion`} />

          <RoundTableFeed title="Live agent round table" max={300} />

          <GlassCard className="p-5" inert>
            <PanelTitle right={<Pill>{mine.length} open</Pill>}>Positions &amp; exit plan</PanelTitle>
            {mine.length ? (
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wide text-white/35">
                    <th className="text-left pb-2">Symbol</th>
                    <th className="text-right pb-2">Qty</th>
                    <th className="text-right pb-2">Entry</th>
                    <th className="text-right pb-2">Stop</th>
                    <th className="text-right pb-2">Target</th>
                    <th className="text-right pb-2">R:R</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {mine.map((p) => (
                    <tr key={p.symbol} className="border-t border-white/[0.06]">
                      <td className="py-1.5 font-sans font-semibold">{p.symbol}</td>
                      <td className="text-right">{p.quantity.toFixed(4)}</td>
                      <td className="text-right">{money(p.entry)}</td>
                      <td className="text-right text-red-400">{money(p.stop)}</td>
                      <td className="text-right text-hood-green">{money(p.target)}</td>
                      <td className="text-right">
                        {(Math.abs(p.target - p.entry) / Math.abs(p.entry - p.stop)).toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty title="No open positions."
                     hint="One is opened only when the round table reaches consensus AND the grader approves the exit plan." />
            )}
          </GlassCard>

          <TradeHistory venue={venue} />
        </div>

        {/* ================= RIGHT — paper (25%) =================
            The border IS the differentiation line the split exists for. */}
        <div className="xl:col-span-1 space-y-5 xl:pl-6 xl:border-l border-white/[0.09] mt-5 xl:mt-0">
          <div className="flex items-center gap-3 px-1">
            <span className="text-[10px] uppercase tracking-[0.16em] text-amber-400/70 font-semibold">
              Paper
            </span>
            <ModeToggle mode="paper" label="Go Paper" state={paperMode} venue={modeKey}
                        gateOpen onSet={(v) => (v ? setPreflight("paper") : setMode("paper", false))} />
            <span className="h-px flex-1 bg-white/[0.09]" />
          </div>

          {poly ? <MarketStance /> : (
            <GlassCard className="p-5" inert>
              <PanelTitle>Agent round table</PanelTitle>
              <AgentRoundTable size={250} activeIds={[]} />
              <div className="text-[11px] text-white/30 text-center mt-2">
                A pulse travels node → centre when that seat speaks. Hover any node for its mandate.
              </div>
            </GlassCard>
          )}

          <GlassCard className="p-5" inert>
            <PanelTitle right={<Pill tone="warn">simulated</Pill>}>
              {title} paper trading engine
            </PanelTitle>
            <div className="grid grid-cols-2 gap-x-4 gap-y-3 mb-4">
              <Stat label="Graded trades"
                    value={`${paper?.graded_paper_trades ?? 0}/${paper?.required ?? 50}`}
                    sub={`${(paper?.pct_complete ?? 0).toFixed(0)}% to the live bar`} />
              <Stat label="Win rate"
                    value={paper?.win_rate == null ? "—" : `${paper.win_rate.toFixed(0)}%`} />
              <Stat label="Realised (paper)" value={signed(paper?.realized_usd)}
                    tone={toneOf(paper?.realized_usd)} />
              <Stat label="Seats calibrated"
                    value={`${paper?.seats_calibrated ?? 0}/${paper?.seats_scored ?? 0}`}
                    sub="self-improvement" />
            </div>
            {/* Rule #13's bar, as a bar. A count reads as trivia; a track that
                is one fifth full reads as "not yet". */}
            <div className="mb-4">
              <div className="h-1.5 rounded-full bg-white/[0.08] overflow-hidden">
                <motion.div className="h-full rounded-full"
                  style={{ background: poly ? "linear-gradient(90deg,#2d52f3,#00c805)"
                                            : `linear-gradient(90deg,${GOLD_DEEP},${GOLD})` }}
                  initial={{ width: 0 }}
                  animate={{ width: `${Math.max(2, paper?.pct_complete ?? 0)}%` }}
                  transition={{ duration: 0.6, ease: "easeOut" }} />
              </div>
            </div>
            {poly
              ? <EquityArea values={paperCurve} colour="#fbbf24" height={180} />
              : <Sparkline values={paperCurve} colour="#fbbf24" height={180} />}
            <div className="text-[10.5px] text-white/40 mt-1.5 text-center">
              Prices are live; fills are simulated.
            </div>
            <div className="text-[11px] text-white/35 mt-3 leading-relaxed border-t border-white/[0.07] pt-3">
              Prices are live from the venue; fills are simulated with a pessimistic
              spread cross. A paper record built on synthetic prices would prove
              nothing about live behaviour.
            </div>
          </GlassCard>

          <AgentDialogue title="Paper round table · live deliberation" />

          <GlassCard className={`p-4 ${fund?.router_live_gate?.live_possible ? "" : "gate-locked"}`} inert>
            <PanelTitle right={fund?.router_live_gate?.live_possible
              ? <Pill tone="good">live possible</Pill>
              : <Pill tone="warn">🔒 paper only</Pill>}>
              Live-trading checklist
            </PanelTitle>
            {fund?.router_live_gate ? (
              <div className="space-y-2">
                {Object.entries(fund.router_live_gate.checks).map(([k, v]) => (
                  <div key={k} className="flex items-center gap-2.5">
                    <DrawnCheck checked={v} />
                    <span className={`text-[11px] ${v ? "text-white/70" : "text-white/35"}`}>
                      {LABELS[k] ?? k}
                      {k === "paper_trades_recorded" &&
                        ` (${fund.router_live_gate!.graded_paper_trades}/${fund.router_live_gate!.required_paper_trades})`}
                    </span>
                  </div>
                ))}
              </div>
            ) : <Empty title="No fund attached." />}
            {fund?.router_live_gate && !fund.router_live_gate.live_possible && (
              <div className="flex items-start gap-2 mt-3 pt-3 border-t border-white/[0.08]">
                <span className="text-[12px] leading-none mt-0.5">🔒</span>
                <span className="text-[10.5px] text-white/45 leading-relaxed">
                  The router refuses live orders while any box is unticked — including
                  closes. &ldquo;It&rsquo;s an exit&rdquo; is not a bypass for the checklist.
                </span>
              </div>
            )}
          </GlassCard>
        </div>
      </div>
    </div>
  );
}

const LABELS: Record<string, string> = {
  env_paper_trading_false: "PAPER_TRADING=false",
  venue_authenticated: "Venue authenticated",
  risk_caps_live_appropriate: "Risk caps sized",
  paper_trades_recorded: "Paper trades on record",
  operator_approval_lesson: "Operator approval",
};

/**
 * GO / STOP for one side of one venue.
 *
 * `attached: false` means no adapter of that mode is registered — execution
 * currently sits on the paper adapter, so the live switch has nothing to act
 * on. It says so rather than offering a button that promises live trading the
 * process cannot do. A closed rule-#13 gate is shown the same way: the switch
 * is a permission, never an override.
 */
function ModeToggle({ mode, label, state, venue, gateOpen, onSet }: {
  mode: "paper" | "live";
  label: string;
  state?: ModeState;
  venue?: string;
  gateOpen: boolean;
  onSet: (on: boolean) => void;
}) {
  // Disabled ONLY when there is no adapter of this mode to act on. A shut
  // rule-#13 gate leaves the button live on purpose: pressing it runs the
  // preflight, which names the check that stopped it. A dead control explains
  // nothing, and "why is this greyed out" is the question it would create.
  const usable = !!venue && !!state?.attached;
  const on = state?.on !== false;
  const why = !venue ? "no venue attached"
    : !state?.attached ? `no ${mode} venue attached`
    : !gateOpen ? "gate shut — press to see why" : "";

  return (
    <div className="flex items-center gap-2">
      <div className={`flex items-center gap-1 p-0.5 rounded-full border border-white/12
                       bg-white/[0.05] ${usable ? "" : "opacity-45"}`}>
        {(["GO", "STOP"] as const).map((k) => {
          const active = (k === "GO") === on;
          return (
            <button key={k} type="button" disabled={!usable}
              aria-pressed={active}
              aria-label={`${mode} trading ${k}`}
              title={k === "GO" ? label : undefined}
              onClick={() => onSet(k === "GO")}
              className="relative px-3 py-[3px] text-[10px] font-bold tracking-wide
                         disabled:cursor-not-allowed">
              {active && (
                <motion.span layoutId={`mode-${mode}-${venue ?? "none"}`}
                  className={`absolute inset-0 rounded-full ${
                    k === "GO" ? "bg-hood-green/25 border border-hood-green/50"
                               : "bg-red-500/20 border border-red-400/40"}`}
                  transition={{ type: "spring", stiffness: 480, damping: 36 }} />
              )}
              <span className={`relative z-10 whitespace-nowrap ${
                active ? (k === "GO" ? "text-hood-green" : "text-red-400") : "text-white/35"}`}>
                {k === "GO" ? label : k}
              </span>
            </button>
          );
        })}
      </div>
      {why && <span className="text-[9.5px] text-white/30">{why}</span>}
    </div>
  );
}

function Readout({ label, value, tone = "text-white/70" }: {
  label: string; value: string; tone?: string;
}) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-[0.14em] text-white/30">{label}</div>
      <div className={`text-[12px] font-medium mt-0.5 ${tone}`}>{value}</div>
    </div>
  );
}

/** Anchor markers to real indices in the series. A ReferenceDot whose x falls
 *  outside the domain silently renders nothing, which looks like "no trades"
 *  rather than like a bug. */
function markersFrom(positions: Position[], curve: number[]): Marker[] {
  if (!curve.length || !positions.length) return [];
  const last = curve.length - 1;
  return positions.slice(0, 3).map((p, i) => ({
    x: Math.max(0, last - i),
    y: curve[Math.max(0, last - i)],
    label: p.symbol,
    tone: "entry" as const,
  }));
}
