import { Fragment, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill, toneOf } from "./primitives";
import { usePoll, type TradeRow, NEAR
} from "../lib/api";

const stamp = (t: number | null) => {
  if (!t) return { d: "—", h: "" };
  const dt = new Date(t * 1000);
  return {
    d: dt.toLocaleDateString(undefined, { month: "short", day: "2-digit" }),
    h: dt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }),
  };
};

/**
 * Closed trades, with the attribution attached.
 *
 * The blame column is the reason this table exists: "the committee was wrong"
 * is not actionable, "the Quant was confidently wrong while the Risk Manager
 * objected" is. Blame lands only on a loss and only on seats whose own signal
 * matched the consensus — the backend refuses to blame a dissenter.
 */
export function TradeHistory({ venue }: { venue: "polymarket_us" | "robinhood" }) {
  const { data } = usePoll<TradeRow[]>("/api/trade-history?limit=40", NEAR);
  const [open, setOpen] = useState<string | null>(null);

  // Prediction markets carry a slug, equities a ticker. Nothing in the row
  // says which venue it was, so the shape of the symbol is the only signal
  // available — and it is honest to show everything rather than guess wrong.
  const rows = data ?? [];

  return (
    <GlassCard className="p-5" inert>
      <PanelTitle right={<Pill>{rows.length} closed</Pill>}>
        History of trades · agent attribution
      </PanelTitle>

      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] min-w-[620px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-white/35">
                <th className="text-left pb-2">Date</th>
                <th className="text-left pb-2">Time</th>
                <th className="text-left pb-2">Symbol</th>
                <th className="text-left pb-2">Side</th>
                <th className="text-right pb-2">Conf</th>
                <th className="text-right pb-2">Result</th>
                <th className="text-left pb-2 pl-4">Blame / credit</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const { d, h } = stamp(r.resolved_at);
                const expanded = open === r.thesis_id;
                return (
                  <Fragment key={r.thesis_id}>
                    <tr
                        onClick={() => setOpen(expanded ? null : r.thesis_id)}
                        className="border-t border-white/[0.06] cursor-pointer hover:bg-white/[0.03]">
                      <td className="py-1.5 font-mono text-white/50">{d}</td>
                      <td className="font-mono text-white/40">{h}</td>
                      <td className="font-semibold">{r.symbol}</td>
                      <td className={r.side === "buy" ? "text-hood-green" : "text-red-400"}>
                        {r.side.toUpperCase()}
                      </td>
                      <td className="text-right font-mono text-white/45">
                        {r.confidence?.toFixed(0) ?? "—"}
                      </td>
                      {/* Sign as well as colour: this is a screen about money
                          and roughly 8% of men cannot separate red from green. */}
                      <td className={`text-right font-mono ${r.won ? "text-hood-green" : "text-red-400"}`}>
                        {r.won ? "✓" : "✗"}{" "}
                        <span className={toneOf(r.realized_pct)}>
                          {r.realized_pct == null ? "—"
                            : `${r.realized_pct > 0 ? "+" : ""}${r.realized_pct.toFixed(2)}%`}
                        </span>
                      </td>
                      <td className="pl-4 text-[11px] text-white/50 max-w-[220px] truncate">
                        {r.blamed.length
                          ? <span className="text-red-400/80">⚠ {r.blamed.map((b) => b.name).join(", ")}</span>
                          : r.vindicated.length
                            ? <span className="text-amber-400/70">↺ {r.vindicated[0].name} dissented</span>
                            : <span className="text-white/25">—</span>}
                      </td>
                    </tr>
                    {expanded && (
                      <tr>
                        <td colSpan={7} className="pb-3">
                          <AnimatePresence>
                            <motion.div initial={{ opacity: 0, height: 0 }}
                              animate={{ opacity: 1, height: "auto" }}
                              className="rounded-xl border border-white/[0.07] bg-white/[0.03] p-3 space-y-2">
                              {r.blamed.map((b) => (
                                <Line key={b.name} tone="bad" who={b.name}
                                      what={`backed the call at ${b.confidence?.toFixed(0) ?? "?"} — ${b.reasoning}`} />
                              ))}
                              {r.vindicated.map((v) => (
                                <Line key={v.name} tone="warn" who={`${v.name} (was right)`}
                                      what={`called ${v.signal} — ${v.reasoning}`} />
                              ))}
                              {r.abstained.length > 0 && (
                                <Line tone="neutral" who="Abstained"
                                      what={r.abstained.join(", ") + " — no call, no blame"} />
                              )}
                              {r.actions.length > 0 && (
                                <div className="pt-2 border-t border-white/[0.07]">
                                  <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1">
                                    Self-improvement actions taken
                                  </div>
                                  {r.actions.map((a, i) => (
                                    <div key={i} className="text-[11px] text-white/60 leading-relaxed">
                                      <span className="text-white/35 font-mono mr-1.5">{a.code}</span>
                                      {a.lesson}
                                    </div>
                                  ))}
                                </div>
                              )}
                              {!r.blamed.length && !r.vindicated.length && !r.actions.length && (
                                <div className="text-[11px] text-white/35">
                                  Won as called — nothing to correct.
                                </div>
                              )}
                            </motion.div>
                          </AnimatePresence>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty title="No closed trades yet."
               hint="A row appears when a thesis resolves — the fund grades itself on results, not on positions it is still hoping about." />
      )}
      <div className="text-[10.5px] text-white/25 mt-3">
        Click a row for the seat-by-seat record. {venue === "robinhood" ? "Equities and crypto." : "Prediction markets."}
      </div>
    </GlassCard>
  );
}

function Line({ who, what, tone }: { who: string; what: string; tone: "bad" | "warn" | "neutral" }) {
  const colour = tone === "bad" ? "text-red-400/85" : tone === "warn" ? "text-amber-400/80" : "text-white/40";
  return (
    <div className="text-[11px] leading-relaxed">
      <span className={`font-semibold ${colour}`}>{who}</span>
      <span className="text-white/55"> · {what}</span>
    </div>
  );
}
