import { GlassCard, PanelTitle } from "../components/GlassCard";
import { AgentRoundTable } from "../components/AgentRoundTable";
import { RoundTableFeed } from "../components/RoundTableFeed";
import { EquityArea, Sparkline, type Marker } from "../components/charts";
import { DrawnCheck, Empty, Pill, Stat, money, signed, toneOf } from "../components/primitives";
import {
  post, usePoll, type Balances, type FundStatus,
  type Position, type Record_,
} from "../lib/api";

type Venue = "polymarket_us" | "robinhood";

/**
 * One component serves both venue pages. They differ in palette and chart type
 * (Polymarket: blue gradient area; Robinhood: stark neon sparkline), not in
 * structure — and duplicating ~250 lines to vary two constants would be a
 * maintenance tax with no payoff.
 */
export function VenueView({ venue }: { venue: Venue }) {
  const poly = venue === "polymarket_us";
  const colour = poly ? "#2d52f3" : "#00c805";
  const title = poly ? "Polymarket" : "Robinhood";

  const { data: fund, refresh } = usePoll<FundStatus>("/api/fund", 4000);
  const { data: bal } = usePoll<Balances>("/api/balances", 15000);
  const { data: rec } = usePoll<Record_>("/api/record");
  const { data: positions } = usePoll<Position[]>("/api/positions");
  const { data: sessions, refresh: refreshSessions } =
    usePoll<{ sessions: Record<string, boolean> }>("/api/venue-sessions", 8000);

  const wallet = bal?.[venue];
  const on = sessions?.sessions?.[venue] !== false;
  const curve = rec?.equity_curve ?? [];
  const mine = (positions ?? []).filter((p) =>
    poly ? p.asset_class === "prediction" : p.asset_class !== "prediction");

  return (
    <div className="grid grid-cols-1 xl:grid-cols-12 gap-5 p-5">
      {/* ------------- left 70% ------------- */}
      <div className="xl:col-span-8 space-y-5">
        <GlassCard liquid className="p-5">
          <div className="flex items-start justify-between mb-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: colour }} />
                <span className="text-[12px] text-white/55">{title}</span>
              </div>
              {wallet?.available ? (
                <div className="text-5xl font-light tracking-tighter font-mono mt-1">
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
            <VenueSwitch venue={venue} on={on} onDone={refreshSessions} />
          </div>

          {poly
            ? <EquityArea values={curve} colour={colour} markers={markersFrom(mine, curve)} height={230} />
            : <Sparkline values={curve} colour={colour} markers={markersFrom(mine, curve)} />}

          <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 mt-5 pt-4 border-t border-white/[0.07]">
            <Stat label="Positions" value={mine.length} />
            <Stat label="Realised" value={signed(rec?.realized_usd)} tone={toneOf(rec?.realized_usd)} />
            <Stat label="Open risk"
                  value={money(mine.reduce((a, p) => a + Math.abs(p.entry - p.stop) * p.quantity, 0))}
                  sub="if every stop hits" />
            <Stat label="Buying power" value={wallet?.available ? money(wallet.cash_usd) : "—"} />
            <Stat label="Cycles" value={fund?.metrics?.cycles ?? 0} />
          </div>
        </GlassCard>

        {!poly && (
          <GlassCard className="p-5" inert>
            <PanelTitle>Agent round table</PanelTitle>
            <AgentRoundTable size={320} activeIds={[]} />
            <div className="text-[11px] text-white/30 text-center mt-2">
              A pulse travels node → centre when that seat speaks. Hover any node for its mandate.
            </div>
          </GlassCard>
        )}

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
      </div>

      {/* ------------- right 30% ------------- */}
      <div className="xl:col-span-4 space-y-5">
        <RoundTableFeed />

        <GlassCard className="p-4" inert>
          <PanelTitle right={<Pill tone="warn">paper</Pill>}>Paper trading engine</PanelTitle>
          <div className="space-y-2 text-[12px]">
            <KV k="Status" v={`${title} · simulated fills`} />
            <KV k="Total invested (paper)"
                v={money(mine.reduce((a, p) => a + p.entry * p.quantity, 0))} />
            <KV k="Expected P&L (paper)" v={signed(rec?.realized_usd)} />
          </div>
          <div className="text-[11px] text-white/35 mt-3 leading-relaxed border-t border-white/[0.07] pt-3">
            Prices are live from Robinhood; fills are simulated. A paper record built on
            synthetic prices would prove nothing about live behaviour.
          </div>
        </GlassCard>

        <GlassCard className="p-4" inert>
          <PanelTitle>Live-trading checklist</PanelTitle>
          {fund?.router_live_gate ? (
            <div className="space-y-2">
              {Object.entries(fund.router_live_gate.checks).map(([k, v]) => (
                <div key={k} className="flex items-center gap-2.5">
                  <DrawnCheck checked={v} />
                  <span className={`text-[11px] ${v ? "text-white/70" : "text-white/35"}`}>
                    {LABELS[k] ?? k}
                  </span>
                </div>
              ))}
            </div>
          ) : <Empty title="No fund attached." />}
        </GlassCard>

        <GlassCard className="p-4" inert>
          <PanelTitle>Execution</PanelTitle>
          <div className="flex gap-2">
            <button onClick={async () => { await post("/api/start"); void refresh(); }}
              className="flex-1 py-2 rounded-xl text-[12px] font-semibold bg-gradient-to-r from-green-400 to-green-600 text-black">
              Go
            </button>
            <button onClick={async () => { await post("/api/stop"); void refresh(); }}
              className="flex-1 py-2 rounded-xl text-[12px] font-semibold bg-gradient-to-r from-red-500 to-red-700">
              Stop
            </button>
          </div>
        </GlassCard>
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

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-white/40">{k}</span>
      <span className="font-mono text-white/85">{v}</span>
    </div>
  );
}

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
