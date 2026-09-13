import { GlassCard, PanelTitle } from "../components/GlassCard";
import { AgentDialogue } from "../components/AgentDialogue";
import { LiveFeed } from "../components/LiveFeed";
import { MarketStance } from "../components/MarketStance";
import { RoundTableFeed } from "../components/RoundTableFeed";
import { TradeHistory } from "../components/TradeHistory";
import { EquityArea, Sparkline, type Marker } from "../components/charts";
import { DrawnCheck, Empty, Pill, Stat, money, signed, toneOf } from "../components/primitives";
import { AgentRoundTable } from "../components/AgentRoundTable";
import { usePolymarketTheme } from "../lib/polymarketTheme";
import { useRobinhoodTheme } from "../lib/robinhoodTheme";
import {
  post, usePoll, type Balances, type FundStatus, type PaperProgress,
  type Position, type Record_,
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

  const { data: fund, refresh } = usePoll<FundStatus>("/api/fund", 4000);
  const { data: bal } = usePoll<Balances>("/api/balances", 15000);
  const { data: rec } = usePoll<Record_>("/api/record");
  const { data: paper } = usePoll<PaperProgress>("/api/paper", 10000);
  const { data: positions } = usePoll<Position[]>("/api/positions");
  const { data: sessions, refresh: refreshSessions } =
    usePoll<{ sessions: Record<string, boolean> }>("/api/venue-sessions", 8000);

  const wallet = bal?.[venue];
  const on = sessions?.sessions?.[venue] !== false;
  const curve = rec?.equity_curve ?? [];
  const paperCurve = paper?.equity_curve ?? [];
  const mine = (positions ?? []).filter((p) =>
    poly ? p.asset_class === "prediction" : p.asset_class !== "prediction");

  return (
    <div className="p-5 space-y-5">
      {/* ---------- execution, centred above the split ---------- */}
      <GlassCard liquid className="p-5">
        <div className="flex flex-col items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full" style={{ background: colour }} />
            <h1 className="text-[13px] font-semibold uppercase tracking-[0.16em] text-white/70">
              {title} · Execution
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={async () => { await post("/api/start"); void refresh(); }}
              className="px-10 py-2.5 rounded-2xl text-[13px] font-bold tracking-wide
                         bg-gradient-to-r from-green-400 to-green-600 text-black
                         hover:brightness-110 transition">
              GO
            </button>
            <button onClick={async () => { await post("/api/stop"); void refresh(); }}
              className="px-10 py-2.5 rounded-2xl text-[13px] font-bold tracking-wide
                         bg-gradient-to-r from-red-500 to-red-700 hover:brightness-110 transition">
              STOP
            </button>
            <VenueSwitch venue={venue} on={on} onDone={refreshSessions} />
          </div>
          <div className="text-[11px] text-white/35">
            {fund?.state ?? "stopped"} · {fund?.session ?? "—"} ·
            {" "}GO starts the cycle; it does not bypass a single gate.
          </div>
        </div>
      </GlassCard>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-0">
        {/* ================= LEFT — live ================= */}
        <div className="space-y-5 xl:pr-6">
          <div className="flex items-center gap-2 px-1">
            <span className="text-[10px] uppercase tracking-[0.16em] text-white/45 font-semibold">
              Live
            </span>
            <span className="h-px flex-1 bg-white/[0.09]" />
          </div>

          <GlassCard liquid className="p-5">
            <div className="flex items-start justify-between mb-3">
              <div>
                <div className="text-[12px] text-white/55">{title} account</div>
                {wallet?.available ? (
                  <div className="venue-figure text-5xl font-light tracking-tighter font-mono mt-1">
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
              : <Sparkline values={curve} colour={colour} markers={markersFrom(mine, curve)} />}

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-5 pt-4 border-t border-white/[0.07]">
              <Stat label="Positions" value={mine.length} />
              <Stat label="Open risk"
                    value={money(mine.reduce((a, p) => a + Math.abs(p.entry - p.stop) * p.quantity, 0))}
                    sub="if every stop hits" />
              <Stat label="Buying power" value={wallet?.available ? money(wallet.cash_usd) : "—"} />
              <Stat label="Cycles" value={fund?.metrics?.cycles ?? 0} />
            </div>
          </GlassCard>

          <LiveFeed venue={venue} title={`${title} live data feed`} />

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

        {/* ================= RIGHT — paper =================
            The border IS the differentiation line the split exists for. */}
        <div className="space-y-5 xl:pl-6 xl:border-l border-white/[0.09] mt-5 xl:mt-0">
          <div className="flex items-center gap-2 px-1">
            <span className="text-[10px] uppercase tracking-[0.16em] text-amber-400/70 font-semibold">
              Paper
            </span>
            <span className="h-px flex-1 bg-white/[0.09]" />
          </div>

          {poly ? <MarketStance /> : (
            <GlassCard className="p-5" inert>
              <PanelTitle>Agent round table</PanelTitle>
              <AgentRoundTable size={320} activeIds={[]} />
              <div className="text-[11px] text-white/30 text-center mt-2">
                A pulse travels node → centre when that seat speaks. Hover any node for its mandate.
              </div>
            </GlassCard>
          )}

          <GlassCard className="p-5" inert>
            <PanelTitle right={<Pill tone="warn">simulated</Pill>}>
              {title} paper trading engine
            </PanelTitle>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
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
            {poly
              ? <EquityArea values={paperCurve} colour="#fbbf24" height={180} />
              : <Sparkline values={paperCurve} colour="#fbbf24" height={180} />}
            <div className="text-[11px] text-white/35 mt-3 leading-relaxed border-t border-white/[0.07] pt-3">
              Prices are live from the venue; fills are simulated with a pessimistic
              spread cross. A paper record built on synthetic prices would prove
              nothing about live behaviour.
            </div>
          </GlassCard>

          <AgentDialogue title="Paper round table · live deliberation" />

          <RoundTableFeed title="Live agent round table" max={420} />

          <GlassCard className="p-4" inert>
            <PanelTitle right={fund?.router_live_gate?.live_possible
              ? <Pill tone="good">live possible</Pill> : <Pill tone="warn">paper only</Pill>}>
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

function VenueSwitch({ venue, on, onDone }: { venue: string; on: boolean; onDone: () => void }) {
  return (
    <button
      onClick={async () => {
        await post(`/api/venue-sessions/${venue}/${on ? "stop" : "start"}`);
        onDone();
      }}
      className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-[11px]
        ${on ? "border-hood-green/40 bg-hood-green/10 text-hood-green"
             : "border-white/15 bg-white/[0.05] text-white/40"}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${on ? "bg-hood-green" : "bg-white/30"}`} />
      trading {on ? "ON" : "OFF"}
    </button>
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
