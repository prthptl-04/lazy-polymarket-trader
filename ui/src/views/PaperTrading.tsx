import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AgentDialogue } from "../components/AgentDialogue";
import { AgentRoundTable } from "../components/AgentRoundTable";
import { GlassCard, PanelTitle } from "../components/GlassCard";
import { Preflight, type Step } from "../components/Preflight";
import { RoundTableFeed } from "../components/RoundTableFeed";
import { TradeHistory } from "../components/TradeHistory";
import { EquityArea, Sparkline } from "../components/charts";
import { DrawnCheck, Empty, Pill, Stat, money, signed, toneOf } from "../components/primitives";
import {
  post, usePoll, type EngineState, type FundStatus, type ModeState,
  type PaperProgress, type Record_, type VenueModes,
} from "../lib/api";
import { RH_GOLD as GOLD, RH_GOLD_DEEP as GOLD_DEEP } from "../lib/robinhoodTheme";

type Venue = "polymarket_us" | "robinhood";

const VENUES: { key: Venue; label: string; skin: "polymarket" | "robinhood"; dot: string }[] = [
  { key: "polymarket_us", label: "Polymarket", skin: "polymarket", dot: "bg-poly-blue" },
  { key: "robinhood", label: "Robinhood", skin: "robinhood", dot: "bg-hood-green" },
];

/**
 * Everything simulated, in one place.
 *
 * The paper engine used to be a rail on each venue page, which asked you to
 * hold two realities at once while reading numbers that decide real money. It
 * is its own tab now: both venues side by side, their own GO/STOP, and the
 * record that has to clear rule #13 before any of it becomes real.
 *
 * Every figure here is a SIMULATED fill against a real quote. That is the only
 * arrangement that makes the 50-trade bar mean anything — a paper record built
 * on synthetic prices would prove nothing about live behaviour, and one built
 * on real fills would not be paper.
 */
export function PaperTrading() {
  const { data: fund } = usePoll<FundStatus>("/api/fund", 4000);
  const { data: paper } = usePoll<PaperProgress>("/api/paper", 10000);
  const { data: modeData, refresh: refreshModes } =
    usePoll<{ modes: VenueModes }>("/api/venue-modes", 8000);
  const { data: engineData } = usePoll<{ engines: Record<string, EngineState> }>("/api/engines", 6000);

  const modes = modeData?.modes ?? {};
  // A venue only has its own paper switch if a PAPER adapter is registered under
  // its name. Today neither is: execution sits on one shared paper adapter, so
  // both columns resolve to it — and say so, because two switches that drive one
  // adapter look independent until you press one and the other moves.
  const paperWired = (k?: string) => !!k && !!modes[k]?.paper?.attached;
  const shared = Object.keys(modes).find(paperWired);
  const keyFor = (v: Venue) => (paperWired(v) ? v : shared);

  const [preflight, setPreflight] = useState<Venue | null>(null);
  const systemOn = fund?.state === "running" || fund?.state === "starting";

  const gate = fund?.router_live_gate;

  return (
    <div className="p-5 space-y-5">
      <AnimatePresence>
        {preflight && (() => {
          const v = VENUES.find((x) => x.key === preflight)!;
          const engine = engineData?.engines?.[v.key];
          const steps: Step[] = [
            { label: "Project धन is running", ok: systemOn,
              detail: systemOn ? undefined : "start the system from the top-right control" },
            { label: `${v.label} engine is off`, ok: true, required: false,
              detail: engine?.on
                ? "engine is connected — its quotes are real, fills stay simulated"
                : "not needed: paper fills are simulated either way" },
            { label: "Seats begin paper trading", ok: true,
              detail: "every fill is simulated; the record counts toward the rule-#13 bar" },
          ];
          return (
            <Preflight key={preflight} title={`${v.label} · paper trading`} steps={steps}
              onComplete={() => void setMode(preflight, true)}
              onDismiss={() => setPreflight(null)} />
          );
        })()}
      </AnimatePresence>

      {/* ---------- §1 header ---------- */}
      <GlassCard liquid className="p-5">
        <div className="flex flex-col lg:flex-row lg:items-center gap-5">
          <div className="flex items-center gap-2.5 lg:w-[230px]">
            <span className="w-2.5 h-2.5 rounded-full bg-amber-400" />
            <h1 className="text-[13px] font-semibold uppercase tracking-[0.16em] text-white/70">
              Paper trading · execution
            </h1>
          </div>
          <div className="flex flex-wrap items-center gap-6">
            <Readout label="Scheduler" value={fund?.state ?? "stopped"}
                     tone={systemOn ? "text-hood-green" : "text-white/45"} />
            <Readout label="Session" value={fund?.session ?? "—"} />
            <Readout label="Graded"
                     value={`${paper?.graded_paper_trades ?? 0}/${paper?.required ?? 50}`} />
            <Readout label="Seats calibrated"
                     value={`${paper?.seats_calibrated ?? 0}/${paper?.seats_scored ?? 0}`} />
          </div>
        </div>
        <div className="text-[11px] text-white/35 mt-3 lg:pl-[242px]">
          Quotes are live from each venue; every fill here is simulated. Nothing on
          this page can move real money — that needs Go&nbsp;Live on the venue page,
          which the rule-#13 checklist still has to allow.
        </div>
      </GlassCard>

      {/* ---------- §2 dual paper market ---------- */}
      <GlassCard liquid className="p-5">
        <PanelTitle right={<Pill tone="warn">simulated fills</Pill>}>
          Dual market · paper feed
        </PanelTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {VENUES.map((v) => (
            <PaperVenuePanel
              key={v.key} venue={v} paper={paper}
              engine={engineData?.engines?.[v.key]}
              mode={modes[keyFor(v.key) ?? ""]?.paper}
              sharedWith={keyFor(v.key) !== v.key ? keyFor(v.key) : undefined}
              onGo={() => setPreflight(v.key)}
              onStop={() => void setMode(v.key, false)} />
          ))}
        </div>
      </GlassCard>

      {/* ---------- §3 the bar ---------- */}
      <GlassCard className="col-span-full p-5" inert>
        <PanelTitle right={<Pill tone={gate?.live_possible ? "good" : "warn"}>
          {paper?.pct_complete?.toFixed(0) ?? 0}% to the live bar
        </Pill>}>
          Paper trading · earning the right to trade real money
        </PanelTitle>
        {paper ? (
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
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-3 mt-5">
              <Stat label="Deliberations held" value={paper.deliberations} />
              <Stat label="Theses resolved" value={paper.resolved} />
              <Stat label="Lessons from losses" value={paper.lessons_learned} />
              <Stat label="Confidence calibration"
                    value={paper.shrink_fit.usable ? paper.shrink_fit.shrink!.toFixed(2) : "—"}
                    sub={paper.shrink_fit.usable ? "fitted from outcomes" : "not yet fittable"} />
            </div>
          </>
        ) : <Empty title="No paper record yet." />}
      </GlassCard>

      {/* ---------- §4 the committee at work ---------- */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <GlassCard className="p-5" inert>
          <PanelTitle>Agent round table</PanelTitle>
          <AgentRoundTable size={260} activeIds={[]} />
          <div className="text-[11px] text-white/30 text-center mt-2">
            A pulse travels node → centre when that seat speaks. Hover any node for its mandate.
          </div>
        </GlassCard>
        <div className="xl:col-span-2">
          <AgentDialogue title="Paper round table · live deliberation" />
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <div className="xl:col-span-2">
          <RoundTableFeed title="Deliberations · paper" max={340} />
        </div>

        {/* ---------- §5 the gate ---------- */}
        <GlassCard className={`p-4 ${gate?.live_possible ? "" : "gate-locked"}`} inert>
          <PanelTitle right={gate?.live_possible
            ? <Pill tone="good">live possible</Pill>
            : <Pill tone="warn">🔒 paper only</Pill>}>
            Live-trading checklist
          </PanelTitle>
          {gate ? (
            <div className="space-y-2">
              {Object.entries(gate.checks).map(([k, v]) => (
                <div key={k} className="flex items-center gap-2.5">
                  <DrawnCheck checked={v} />
                  <span className={`text-[11px] ${v ? "text-white/70" : "text-white/35"}`}>
                    {LABELS[k] ?? k}
                    {k === "paper_trades_recorded" &&
                      ` (${gate.graded_paper_trades}/${gate.required_paper_trades})`}
                  </span>
                </div>
              ))}
            </div>
          ) : <Empty title="No fund attached." />}
          {gate && !gate.live_possible && (
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

      {/* ---------- §6 the record ---------- */}
      <TradeHistory venue="robinhood" />
    </div>
  );

  async function setMode(venue: Venue, on: boolean) {
    const key = keyFor(venue);
    if (!key) return;
    await post(`/api/venue-sessions/${key}/paper/${on ? "start" : "stop"}`);
    void refreshModes();
  }
}

/** One venue's paper column: its switch, its simulated curve, its own record. */
function PaperVenuePanel({ venue, paper, engine, mode, sharedWith, onGo, onStop }: {
  venue: { key: Venue; label: string; skin: "polymarket" | "robinhood"; dot: string };
  paper?: PaperProgress | null;
  engine?: EngineState;
  mode?: ModeState;
  /** Set when this switch drives an adapter registered under another name. */
  sharedWith?: string;
  onGo: () => void;
  onStop: () => void;
}) {
  const { data: rec } = usePoll<Record_>(`/api/record?venue=${venue.key}`, 10000);
  const hood = venue.skin === "robinhood";
  const on = mode?.on !== false;
  const usable = !!mode?.attached;

  return (
    <div data-venue-skin={venue.skin} className="flex flex-col">
      <div className="flex items-center gap-2 mb-2">
        <span className={`w-2 h-2 rounded-full ${venue.dot}`} />
        <span className="text-[12px] text-white/70">{venue.label}</span>
        <Pill tone="warn">paper</Pill>
        <div className="ml-auto flex items-center gap-1 p-0.5 rounded-full border border-white/12
                        bg-white/[0.05]">
          {(["GO", "STOP"] as const).map((k) => {
            const active = (k === "GO") === on;
            return (
              <button key={k} type="button" disabled={!usable}
                aria-pressed={active} aria-label={`${venue.key} paper ${k}`}
                onClick={() => (k === "GO" ? onGo() : onStop())}
                className="relative px-3 py-[3px] text-[10px] font-bold tracking-wide
                           disabled:cursor-not-allowed">
                {active && (
                  <motion.span layoutId={`paper-${venue.key}`}
                    className={`absolute inset-0 rounded-full ${
                      k === "GO" ? "bg-hood-green/25 border border-hood-green/50"
                                 : "bg-red-500/20 border border-red-400/40"}`}
                    transition={{ type: "spring", stiffness: 480, damping: 36 }} />
                )}
                <span className={`relative z-10 ${
                  active ? (k === "GO" ? "text-hood-green" : "text-red-400") : "text-white/35"}`}>
                  {k}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="figure-metal font-mono text-3xl font-light tracking-tighter mb-1">
        {signed(rec?.realized_usd)}
      </div>
      <div className="text-[10px] text-white/30 mb-2 leading-relaxed">
        Realised on simulated fills. Quotes are {engine?.on ? "live from the venue" : "provider prices — engine off"};
        the fills never are.
      </div>

      <div className="venue-inset flex-1 min-h-[240px] -mx-2 p-2">
        {hood
          ? <Sparkline values={rec?.equity_curve ?? []} colour={GOLD}
                       gradient={[GOLD, GOLD_DEEP]} height="100%" />
          : <EquityArea values={rec?.equity_curve ?? []} colour="#2d52f3" height="100%" />}
      </div>

      <div className="venue-inset grid grid-cols-2 sm:grid-cols-3 gap-x-5 gap-y-4 mt-3 -mx-2 p-4">
        <Stat label="Paper trades" value={rec?.closed ?? 0}
              sub={rec?.closed ? `${rec.wins}W · ${rec.losses}L` : "nothing closed here"} />
        <Stat label="Win rate"
              value={rec?.win_rate == null ? "—" : `${rec.win_rate.toFixed(0)}%`} />
        <Stat label="Realised" value={signed(rec?.realized_usd)} tone={toneOf(rec?.realized_usd)} />
        <Stat label="Best / worst" value={`${signed(rec?.best_usd)} / ${signed(rec?.worst_usd)}`} />
        <Stat label="Profit factor"
              value={rec?.profit_factor == null ? "—" : rec.profit_factor.toFixed(2)}
              sub="gross win ÷ gross loss" />
        <Stat label="Starting bankroll" value={money(rec?.equity_curve?.[0])} />
      </div>

      {!usable ? (
        <div className="text-[10px] text-white/30 mt-2">
          No paper venue attached for {venue.label}.
        </div>
      ) : sharedWith ? (
        <div className="text-[10px] text-white/30 mt-2">
          Driving the shared <span className="font-mono">{sharedWith}</span> adapter —
          both venues execute on it, so this switch moves the other one too.
        </div>
      ) : null}
      {paper?.shrink_fit && !paper.shrink_fit.usable && (
        <div className="text-[10px] text-white/25 mt-2">{paper.shrink_fit.reason}</div>
      )}
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

const LABELS: Record<string, string> = {
  env_paper_trading_false: "PAPER_TRADING=false",
  venue_authenticated: "Venue authenticated",
  risk_caps_live_appropriate: "Risk caps sized",
  paper_trades_recorded: "Paper trades on record",
  operator_approval_lesson: "Operator approval",
};
