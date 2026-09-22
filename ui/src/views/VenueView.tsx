import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Preflight, type Step } from "../components/Preflight";
import { GlassCard, PanelTitle } from "../components/GlassCard";
import { LiveFeed } from "../components/LiveFeed";
import { MarketStance } from "../components/MarketStance";
import { LiveDebate } from "../components/LiveDebate";
import { CatalystsInFocus } from "../components/Catalysts";
import { Universe } from "../components/Universe";
import { Balance } from "../components/Balance";
import { Provenance } from "../components/Provenance";
import { LivePrices } from "../components/LivePrices";
import { RoundTableFeed } from "../components/RoundTableFeed";
import { RoundTableThread } from "../components/RoundTableThread";
import { TradeHistory } from "../components/TradeHistory";
import { EquityArea, Sparkline, type Marker } from "../components/charts";
import { Empty, Pill, Stat, money, signed, toneOf } from "../components/primitives";
import { usePolymarketTheme } from "../lib/polymarketTheme";
import { RH_GOLD as GOLD, RH_GOLD_DEEP as GOLD_DEEP, useRobinhoodTheme } from "../lib/robinhoodTheme";
import {
  post, usePoll, type Balances, type EngineState, type FundStatus, type ModeState,
  type Position, type Record_, type VenueModes, LIVE, NEAR
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

  const { data: fund } = usePoll<FundStatus>("/api/fund", LIVE);
  const { data: bal } = usePoll<Balances>("/api/balances", 15000);
  const { data: rec } = usePoll<Record_>("/api/record", LIVE);
  const { data: positions } = usePoll<Position[]>("/api/positions", LIVE);
  const { data: modeData, refresh: refreshModes } =
    usePoll<{ modes: VenueModes }>("/api/venue-modes", NEAR);
  const { data: engineData } = usePoll<{ engines: Record<string, EngineState> }>("/api/engines", NEAR);
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
  const [preflight, setPreflight] = useState<null | "live">(null);
  const systemOn = fund?.state === "running" || fund?.state === "starting";

  // The checks the toast walks through. They are READ from live state rather
  // than assumed, so a step that says "on" means the API said so a moment ago.
  const steps: Step[] = [
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
  ];

  const setMode = async (mode: "paper" | "live", start: boolean) => {
    if (!modeKey) return;
    await post(`/api/venue-sessions/${modeKey}/${mode}/${start ? "start" : "stop"}`);
    void refreshModes();
  };
  const curve = rec?.equity_curve ?? [];
  const mine = (positions ?? []).filter((p) =>
    poly ? p.asset_class === "prediction" : p.asset_class !== "prediction");

  return (
    <div className="p-5 space-y-5">
      <AnimatePresence>
        {preflight && (
          <Preflight
            key={preflight}
            title={`${title} · going live`}
            steps={steps}
            onComplete={() => void setMode("live", true)}
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
      {/* One column. Everything simulated moved to the Paper Trading tab —
          a venue page that showed both was asking you to hold two realities at
          once while reading numbers that decide real money. */}
      <div className="grid grid-cols-1">
        <div className="space-y-5">
          <div className="flex items-center gap-3 px-1">
            <span className="text-[10px] uppercase tracking-[0.16em] text-white/45 font-semibold">
              Live
            </span>
            {/* Live cannot run without the connection it trades through, so
                the control is disabled AND reads STOP while the engine is down
                — not merely refused on press. Showing GO as the active side
                would claim a live session that cannot exist. */}
            <ModeToggle mode="live" label="Go Live" state={liveMode} venue={modeKey} page={venue}
                        blockedBy={engine?.on ? null : "engine off — start it on the Overview"}
                        gateOpen={!!fund?.router_live_gate?.live_possible}
                        onSet={(v) => (v ? setPreflight("live") : setMode("live", false))} />
            <span className="h-px flex-1 bg-white/[0.09]" />
          </div>

          <GlassCard liquid className="p-5">
            <div className="flex items-start justify-between mb-3">
              <div>
                <div className="text-[12px] text-white/55">{title} account</div>
                {wallet?.available ? (
                  <div className="venue-figure figure-metal text-5xl font-light tracking-tighter font-mono mt-1">
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

          {/* The instruments actually under debate, priced and charted. The
              equity curve is a single point until something closes, so this is
              the only chart on the page with anything to draw for now. */}
          <LivePrices venue={venue} />

          {/* Why the price is where it is. Sits directly above the debate
              because it is the evidence the Catalyst seat is reading. */}
          {/* What the screen found, before any model was consulted. */}
          <Universe />

          <CatalystsInFocus venue={venue} />

          {/* The other half of a transcript: how old the evidence was. */}
          <Provenance />

          {/* The arithmetic behind the last verdict, beside the live debate. */}
          <Balance />

          <LiveDebate />

          {poly && <MarketStance />}

          <RoundTableThread title={`${title} live round table · discussion`} />

          {/* LIVE side: live reasoning only. The divider on this page is
              load-bearing, and it has to reach the argument as well as the
              number. */}
          <RoundTableFeed title="Live agent round table" max={300} mode="live" />

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

      </div>
    </div>
  );
}


/**
 * GO / STOP for one side of one venue.
 *
 * `attached: false` means no adapter of that mode is registered — execution
 * currently sits on the paper adapter, so the live switch has nothing to act
 * on. It says so rather than offering a button that promises live trading the
 * process cannot do. A closed rule-#13 gate is shown the same way: the switch
 * is a permission, never an override.
 */
function ModeToggle({ mode, label, state, venue, page, gateOpen, blockedBy = null, onSet }: {
  mode: "paper" | "live";
  label: string;
  state?: ModeState;
  venue?: string;
  /** A precondition this mode cannot run without, or null. When set the toggle
   *  is disabled and reads STOP whatever the stored mode says — which is also
   *  the truth, since the engine being down switches the venue session off and
   *  the router refuses every open. */
  blockedBy?: string | null;
  /** The PAGE this toggle is on. Both venue pages resolve `venue` to the same
   *  shared paper adapter, so keying the layout animation on that made the two
   *  pages' pills one shared element across a view switch. */
  page: string;
  gateOpen: boolean;
  onSet: (on: boolean) => void;
}) {
  // Disabled ONLY when there is no adapter of this mode to act on. A shut
  // rule-#13 gate leaves the button live on purpose: pressing it runs the
  // preflight, which names the check that stopped it. A dead control explains
  // nothing, and "why is this greyed out" is the question it would create.
  const usable = !!venue && !!state?.attached && !blockedBy;
  // Effective, not stored: with the precondition unmet nothing can open, so
  // the switch shows the state the fund is actually in.
  const on = !blockedBy && state?.on !== false;
  const why = !venue ? "no venue attached"
    : !state?.attached ? `no ${mode} venue attached`
    : blockedBy ? blockedBy
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
                <motion.span layoutId={`mode-${mode}-${page}`}
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
