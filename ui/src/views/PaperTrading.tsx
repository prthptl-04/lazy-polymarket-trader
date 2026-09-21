import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AgentRoundTable } from "../components/AgentRoundTable";
import { LabDeliberation } from "../components/LabDeliberation";
import { LabCurve } from "../components/labChart";
import { Preflight } from "../components/Preflight";
import { TradeHistory } from "../components/TradeHistory";
import { Bento, LabProgress, LogLine, PulseDot, Readout, Skeleton } from "../components/lab";
import { DrawnCheck, signed, toneOf } from "../components/primitives";
import { useSimulationLab } from "../lib/labTheme";
import { paperPreflightSteps } from "../lib/preflight";
import {
  post, usePoll, type Deliberation, type Edge, type EngineState, type FundStatus,
  type ModeState, type PaperProgress, type Record_, type SeatAgreement, type VenueModes, LIVE, NEAR
} from "../lib/api";

const VENUE = { key: "robinhood", label: "Robinhood" } as const;

/**
 * The Simulation Lab.
 *
 * A quant review and an architecture review reshaped what belongs here. The
 * page is built around one claim it must never overstate: **none of this is
 * real yet**, and most of these numbers cannot mean anything at the current
 * sample. So every statistic either clears its own bar or says which bar it
 * has not cleared.
 *
 * What is deliberately absent: profit factor (on a 2xATR stop / 3xATR target,
 * 3W/2L returns ~2.25 mechanically, with no skill in it), a Polymarket column
 * (that venue is retired — trading/venues/retired.py), and per-seat blame
 * rates (the fund is long-only, so every bullish seat is scored on the
 * identical event).
 */
export function PaperTrading() {
  useSimulationLab(true);

  const { data: fund } = usePoll<FundStatus>("/api/fund", LIVE);
  const { data: paper } = usePoll<PaperProgress>("/api/paper", NEAR);
  const { data: rec } = usePoll<Record_>("/api/record?venue=robinhood", LIVE);
  const { data: edge } = usePoll<Edge>("/api/edge", 15000);
  const { data: agreement } = usePoll<SeatAgreement>("/api/seat-agreement", 20000);
  const { data: delibs } = usePoll<Deliberation[]>("/api/deliberations?limit=12", NEAR);
  const { data: modeData, refresh: refreshModes } =
    usePoll<{ modes: VenueModes }>("/api/venue-modes", NEAR);
  const { data: engineData } = usePoll<{ engines: Record<string, EngineState> }>("/api/engines", NEAR);

  const [preflight, setPreflight] = useState(false);
  const systemOn = fund?.state === "running" || fund?.state === "starting";
  const engine = engineData?.engines?.[VENUE.key];
  const modes = modeData?.modes ?? {};
  const paperKey = Object.keys(modes).find((k) => modes[k]?.paper?.attached);
  const mode: ModeState | undefined = paperKey ? modes[paperKey].paper : undefined;
  const running = mode?.on !== false && systemOn;
  const gate = fund?.router_live_gate;

  const steps = paperPreflightSteps({
    systemOn, paperAttached: !!mode?.attached,
    engineOn: !!engine?.on, engineAuthed: !!engine?.authenticated, venue: VENUE.label,
  });

  const setMode = async (on: boolean) => {
    if (!paperKey) return;
    await post(`/api/venue-sessions/${paperKey}/paper/${on ? "start" : "stop"}`);
    void refreshModes();
  };

  return (
    <div className="p-4 space-y-3.5">
      <AnimatePresence>
        {preflight && (
          <Preflight title={`${VENUE.label} · paper trading`} steps={steps}
                     onComplete={() => void setMode(true)}
                     onDismiss={() => setPreflight(false)} />
        )}
      </AnimatePresence>

      {/* ---- header: what the engine is doing, and that none of it is real ---- */}
      <div className="lab-panel px-4 py-3 flex flex-wrap items-center gap-x-8 gap-y-3">
        <div className="flex items-center gap-2.5 min-w-[210px]">
          <PulseDot live={!!running} />
          <div>
            <h1 className="text-[13px] font-semibold uppercase tracking-[0.15em]
                           text-[var(--lab-ink)]">Simulation lab</h1>
            <div className="text-[10px] text-[var(--lab-muted)] font-mono">
              every fill here is simulated
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1 p-0.5 rounded-full border border-white/10
                        bg-white/[0.04]">
          {(["GO", "STOP"] as const).map((k) => {
            const active = (k === "GO") === (mode?.on !== false);
            return (
              <button key={k} type="button" disabled={!mode?.attached}
                aria-pressed={active} aria-label={`paper ${k}`}
                onClick={() => (k === "GO" ? setPreflight(true) : void setMode(false))}
                className="relative px-3.5 py-1 text-[10px] font-bold tracking-wide font-mono
                           disabled:cursor-not-allowed">
                {active && (
                  <motion.span layoutId="lab-mode"
                    className={`absolute inset-0 rounded-full border ${
                      k === "GO" ? "bg-[var(--lab-amber)]/20 border-[var(--lab-amber)]/50"
                                 : "bg-red-500/15 border-red-400/40"}`}
                    transition={{ type: "spring", stiffness: 480, damping: 36 }} />
                )}
                <span className={`relative z-10 ${
                  active ? (k === "GO" ? "text-[var(--lab-amber)]" : "text-red-400")
                         : "text-[var(--lab-muted)]"}`}>{k}</span>
              </button>
            );
          })}
        </div>

        <Readout label="Scheduler" value={fund?.state ?? "stopped"} />
        <Readout label="Session" value={fund?.session ?? "—"} />
        <Readout label="Round trips"
                 value={`${gate?.graded_paper_trades ?? 0}/${gate?.required_paper_trades ?? 50}`}
                 sub="closed, not orders" amber />
        <Readout label="Fill rate"
                 value={gate?.orders?.fill_rate_pct == null ? "—"
                        : `${gate.orders.fill_rate_pct.toFixed(0)}%`}
                 sub={`${gate?.orders?.orders_filled ?? 0} of ${gate?.orders?.orders_graded ?? 0} graded`} />
        <div className="text-[10px] text-[var(--lab-muted)] max-w-sm leading-relaxed ml-auto">
          Nothing on this page can move money. Live trading needs the venue engine,
          Go&nbsp;Live on the venue page, and the rule-#13 checklist.
        </div>
      </div>

      {/* ---- the bento grid ---- */}
      <div className="lab-grid">
        <Bento span={8} title="Simulated equity" mono
               right={<span className="font-mono text-[11px] text-[var(--lab-muted)]">
                 {rec?.closed ?? 0} closed · {signed(rec?.realized_usd)}
               </span>}>
          <div className="h-[300px]">
            <LabCurve values={rec?.equity_curve ?? []} live={!!running} />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-3 pt-3 border-t border-white/[0.07]">
            <Readout label="Realised (net)" value={signed(rec?.realized_usd)}
                     tone={toneOf(rec?.realized_usd)} />
            <Readout label="Best / worst"
                     value={`${signed(rec?.best_usd)} / ${signed(rec?.worst_usd)}`} />
            <Readout label="Top trade share"
                     value={rec?.concentration?.top1_share_pct == null ? "—"
                            : `${rec.concentration.top1_share_pct.toFixed(0)}%`}
                     sub={rec?.concentration?.reason ?? rec?.concentration?.top1_symbol ?? undefined} />
            <Readout label="Max concurrent" value={rec?.concentration?.max_concurrent ?? 0} />
          </div>
          {(rec?.concentration?.top1_share_pct ?? 0) > 50 && (
            <div className="text-[10.5px] text-[var(--lab-amber)] mt-2">
              One trade is more than half of this record. This page describes that trade.
            </div>
          )}
        </Bento>

        <Bento span={4} title="Is it luck?" mono>
          {edge ? (
            <>
              <Readout label="Expectancy" amber
                       value={edge.mean_r == null ? "—" : `${edge.mean_r.toFixed(2)}R`}
                       sub={edge.verdict} />
              <div className="grid grid-cols-2 gap-3 mt-3">
                <Readout label="Closed" value={edge.n} sub={`${edge.wins}W`} />
                <Readout label="Binomial p"
                         value={edge.binomial_p == null ? "—" : edge.binomial_p.toFixed(3)}
                         sub={edge.p0 ? `vs break-even ${(edge.p0 * 100).toFixed(0)}%` : undefined} />
                <Readout label="t" value={edge.t_stat == null ? "—" : edge.t_stat.toFixed(2)} />
                <Readout label="Bootstrap p5"
                         value={edge.bootstrap_p5_mean_r == null ? "—"
                                : `${edge.bootstrap_p5_mean_r.toFixed(2)}R`}
                         sub="mean R on a bad draw" />
              </div>
              {!!edge.r_values.length && (
                <div className="mt-3 pt-3 border-t border-white/[0.07]">
                  <div className="lab-label mb-1.5">Per-trade R</div>
                  <div className="flex items-end gap-1 h-14">
                    {edge.r_values.map((r, i) => (
                      <div key={i} className="flex-1 rounded-sm"
                           style={{ height: `${Math.min(100, Math.abs(r) * 28)}%`,
                                    background: r >= 0 ? "var(--lab-amber)" : "#fb7185",
                                    alignSelf: r >= 0 ? "flex-end" : "flex-start" }}
                           title={`${r.toFixed(2)}R`} />
                    ))}
                  </div>
                </div>
              )}
              <div className="text-[10px] text-[var(--lab-muted)] mt-3 leading-relaxed">
                {edge.note}
                {edge.n_for_significance && ` At this dispersion, |t|>2 needs about
                 ${edge.n_for_significance} trades.`}
              </div>
            </>
          ) : <Skeleton lines={6} />}
        </Bento>

        <Bento span={7} rows={2} title="Round table · live deliberation" className="min-h-[380px]">
          <LabDeliberation live={!!running} />
        </Bento>

        <Bento span={5} title="Do the seats disagree?" mono>
          {agreement ? (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Readout label="Mean κ"
                         value={agreement.mean_kappa == null ? "—" : agreement.mean_kappa.toFixed(2)}
                         sub="0 independent · 1 one voice" amber />
                <Readout label="Raw agreement"
                         value={agreement.mean_agreement_pct == null ? "—"
                                : `${agreement.mean_agreement_pct.toFixed(0)}%`}
                         sub={`${agreement.n_deliberations} deliberations`} />
              </div>
              <div className="space-y-1 mt-3 max-h-[120px] overflow-y-auto pr-1">
                {agreement.pairs.slice(0, 6).map((p) => (
                  <LogLine key={`${p.a}-${p.b}`} tag={p.duplicate ? "dup" : "ok"}
                           tone={p.duplicate ? "var(--lab-amber)" : "var(--lab-muted)"}>
                    <span className="font-mono">{p.a} ~ {p.b}</span>{" "}
                    κ {p.kappa == null ? "—" : p.kappa.toFixed(2)} · n {p.n}
                  </LogLine>
                ))}
              </div>
              <div className="text-[10px] text-[var(--lab-muted)] mt-2.5 leading-relaxed">
                {agreement.reason ?? "Needs no outcomes — the only committee measure computable today. Above 0.7 the seats are one opinion with several voices, and six model calls per candidate buy no diversification."}
              </div>
            </>
          ) : <Skeleton lines={5} />}
        </Bento>

        <Bento span={5} title="Cost of trading" mono>
          {rec?.bridge ? (
            rec.bridge.reason ? (
              <div className="text-[11.5px] text-[var(--lab-muted)] leading-relaxed py-4">
                {rec.bridge.reason}
                <div className="text-[10px] mt-1.5">
                  A bridge showing $0.00 of cost would be the lie this panel exists to prevent.
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-3 gap-3">
                <Readout label="Gross (mid)" value={signed(rec.bridge.gross_usd)} />
                <Readout label="Cost" value={signed(-(rec.bridge.cost_usd ?? 0))}
                         tone="text-red-400" />
                <Readout label="Net (fills)" value={signed(rec.bridge.net_usd)}
                         tone={toneOf(rec.bridge.net_usd)} amber />
              </div>
            )
          ) : <Skeleton lines={3} />}
        </Bento>

        <Bento span={4} title="Agent round table">
          <AgentRoundTable size={230} activeIds={[]} />
        </Bento>

        <Bento span={8} title="Graduation to real capital" mono
               right={<span className="font-mono text-[11px] text-[var(--lab-muted)]">
                 {(paper?.pct_complete ?? 0).toFixed(0)}% of the trade count
               </span>}>
          <LabProgress pct={paper?.pct_complete ?? 0}
                       label={`${gate?.graded_paper_trades ?? 0} of ${gate?.required_paper_trades ?? 50} closed round trips — the operational bar, not the statistical one`} />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 mt-4">
            {(gate?.graduation ?? []).map((g) => (
              <div key={g.id} className="flex items-start gap-2.5">
                <DrawnCheck checked={g.ok} />
                <div className="min-w-0">
                  <div className={`text-[11px] ${g.ok ? "text-[var(--lab-ink)]/80"
                                                      : "text-[var(--lab-muted)]"}`}>
                    {g.label}
                  </div>
                  <div className="text-[9.5px] text-[var(--lab-muted)]/80 leading-snug">
                    {g.detail}
                  </div>
                </div>
              </div>
            ))}
          </div>
          {!gate && <div className="text-[11px] text-[var(--lab-muted)] py-3">No fund attached.</div>}
        </Bento>

        <Bento span={12} title="Deliberation log" mono>
          {delibs?.length ? (
            <div className="space-y-1 max-h-[200px] overflow-y-auto pr-1">
              {delibs.map((d) => (
                <LogLine key={d.thesis_id}
                         at={d.created ? new Date(d.created * 1000).toLocaleTimeString() : undefined}
                         tag={d.signal ?? d.status}
                         tone={d.signal === "bullish" ? "#34d399"
                               : d.signal === "bearish" ? "#fb7185" : "var(--lab-muted)"}>
                  <span className="font-mono">{d.symbol}</span> · {d.seats} seats
                  {d.confidence != null && <> · stated {d.confidence.toFixed(0)}% (uncalibrated)</>}
                </LogLine>
              ))}
            </div>
          ) : (
            <div className="text-[11.5px] text-[var(--lab-muted)] py-4">
              No deliberations yet. The committee convenes once a cycle finds a candidate
              that survives the deterministic screens.
            </div>
          )}
        </Bento>

        <Bento span={12} title="Closed trades · agent attribution">
          <TradeHistory venue="robinhood" />
        </Bento>
      </div>
    </div>
  );
}
