import { motion } from "framer-motion";
import { GlassCard, PanelTitle } from "../components/GlassCard";
import { EquityArea } from "../components/charts";
import { DrawnCheck, Empty, Pill, Stat, money, signed, toneOf } from "../components/primitives";
import {
  usePoll, type Agent, type Balances, type FundStatus, type Lesson,
  type PaperProgress, type Record_,
} from "../lib/api";

export function Overview() {
  const { data: rec } = usePoll<Record_>("/api/record");
  const { data: paper } = usePoll<PaperProgress>("/api/paper");
  const { data: fund } = usePoll<FundStatus>("/api/fund", 4000);
  const { data: bal } = usePoll<Balances>("/api/balances", 15000);
  const { data: agents } = usePoll<Agent[]>("/api/agents", 60000);
  const { data: lessons } = usePoll<Lesson[]>("/api/lessons?limit=6", 12000);

  const curve = rec?.equity_curve ?? [];
  const gate = fund?.router_live_gate;

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-4 gap-5 p-5">

      {/* ---------------- dual market feed ---------------- */}
      <GlassCard liquid className="md:col-span-2 xl:col-span-3 p-5">
        <PanelTitle right={<Pill>{fund?.session ?? "—"}</Pill>}>
          Dual market · live feed
        </PanelTitle>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-2 h-2 rounded-full bg-poly-blue" />
              <span className="text-[12px] text-white/70">Polymarket</span>
            </div>
            <EquityArea values={curve} colour="#2d52f3" />
          </div>
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-2 h-2 rounded-full bg-hood-green" />
              <span className="text-[12px] text-white/70">Robinhood</span>
            </div>
            <EquityArea values={curve} colour="#00c805" />
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

      {/* ---------------- gates ---------------- */}
      <GlassCard className="p-5">
        <PanelTitle>Gates · allowed actions</PanelTitle>
        {gate ? (
          <>
            <div className="space-y-2.5">
              {Object.entries(gate.checks).map(([k, v]) => (
                <div key={k} className="flex items-center gap-2.5">
                  <DrawnCheck checked={v} />
                  <span className={`text-[12px] ${v ? "text-white/75" : "text-white/40"}`}>
                    {LABELS[k] ?? k}
                  </span>
                </div>
              ))}
            </div>
            <div className="mt-4 pt-3 border-t border-white/[0.07]">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-white/40">Graded paper trades</span>
                <span className="font-mono text-white/80">
                  {gate.graded_paper_trades} / {gate.required_paper_trades}
                </span>
              </div>
              {!gate.live_possible && (
                <div className="text-[11px] text-amber-400/90 mt-2.5 leading-relaxed">
                  Live trading blocked. The fund is paper-only until every box is ticked.
                </div>
              )}
            </div>
          </>
        ) : <Empty title="No fund attached." hint="Set ANTHROPIC_API_KEY and a watchlist, then restart." />}
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

      {/* ---------------- agents ---------------- */}
      <GlassCard className="col-span-full p-5">
        <PanelTitle right={paper ? <Pill tone={paper.seats_calibrated ? "good" : "neutral"}>
          {paper.seats_calibrated} of {paper.seats_scored} calibrated
        </Pill> : undefined}>
          Agents performance
        </PanelTitle>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2.5">
          {(agents ?? []).map((a) => {
            const s = paper?.seats.find((x) => x.seat_id === a.id);
            const scored = !!s?.samples;
            const good = scored && s!.hit_rate >= 0.5;
            return (
              <motion.div key={a.id} whileHover={{ y: -2 }}
                className="rounded-2xl border border-white/[0.08] p-3"
                style={{ background: !scored ? "rgba(255,255,255,0.03)"
                  : good ? "linear-gradient(160deg, rgba(0,200,5,0.10), rgba(255,255,255,0.02))"
                         : "linear-gradient(160deg, rgba(248,113,113,0.10), rgba(255,255,255,0.02))" }}>
                <div className="text-[15px] leading-none">{a.icon}</div>
                <div className="text-[11px] text-white/70 mt-1.5 leading-tight">{a.name}</div>
                <div className={`font-mono text-[14px] mt-1 ${scored ? (good ? "text-hood-green" : "text-red-400") : "text-white/25"}`}>
                  {scored ? `${(s!.hit_rate * 100).toFixed(0)}%` : "—"}
                </div>
                <div className="text-[9px] text-white/30 mt-0.5">
                  {scored ? `${s!.samples} calls · brier ${s!.brier.toFixed(2)}` : "unscored"}
                </div>
              </motion.div>
            );
          })}
        </div>
      </GlassCard>

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
