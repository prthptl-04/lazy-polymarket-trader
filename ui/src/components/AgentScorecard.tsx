import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Avatar } from "./Avatar";
import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill } from "./primitives";
import { usePoll, type AgentMatrixRow } from "../lib/api";

const pct = (v: number | null) => (v == null ? "—" : `${v.toFixed(0)}%`);

/**
 * Per-seat examination, in three tenses.
 *
 * Past is the lifetime score, present is the last ten debates against
 * everything before them, and future is what the next calls have to look like.
 * The middle one is the honest one: a seat cannot influence a lifetime average
 * any more, and judging it on one hides the seat that has started improving.
 *
 * `applied` and `flagged` are kept apart on purpose. Applied is machinery that
 * runs today; flagged is a recommendation. Printing advice as enforcement would
 * make this panel describe a fund that does not exist.
 */
export function AgentScorecard() {
  const { data } = usePoll<AgentMatrixRow[]>("/api/agents/matrix", 20000);
  const [open, setOpen] = useState<string | null>(null);

  if (!data?.length) {
    return (
      <GlassCard className="col-span-full p-5" inert>
        <PanelTitle>Agents performance</PanelTitle>
        <Empty title="No roster." />
      </GlassCard>
    );
  }

  const scored = data.filter((a) => a.samples > 0).length;

  return (
    <GlassCard className="col-span-full p-5" inert>
      <PanelTitle right={<Pill tone={scored ? "good" : "neutral"}>{scored} of {data.length} scored</Pill>}>
        Agents performance · individual examination
      </PanelTitle>

      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
        {data.map((a) => {
          const good = a.hit_rate != null && a.hit_rate >= 50;
          const expanded = open === a.id;
          return (
            <motion.button key={a.id} type="button"
              onClick={() => setOpen(expanded ? null : a.id)}
              whileHover={{ y: -2 }}
              aria-expanded={expanded}
              className={`rounded-2xl border p-3 text-left transition-colors ${
                expanded ? "border-white/25 bg-white/[0.07]" : "border-white/[0.08] hover:bg-white/[0.04]"}`}
              style={{ background: !a.samples ? undefined
                : good ? "linear-gradient(160deg, rgba(0,200,5,0.10), rgba(255,255,255,0.02))"
                       : "linear-gradient(160deg, rgba(248,113,113,0.10), rgba(255,255,255,0.02))" }}>
              {/* Larger faces than the ring: this is the panel you read, not glance at. */}
              <Avatar seed={a.name} size={56} className="w-14 h-14 border border-white/12 mb-2" />
              <div className="text-[11px] text-white/75 leading-tight">{a.name}</div>
              <div className={`font-mono text-[17px] mt-1 ${
                !a.samples ? "text-white/25" : good ? "text-hood-green" : "text-red-400"}`}>
                {pct(a.hit_rate)}
              </div>
              <div className="text-[9px] text-white/35 mt-0.5">
                {a.samples ? `${a.samples} calls · brier ${a.brier?.toFixed(2)}` : "unscored"}
              </div>
              {a.improvement_pts != null && (
                <div className={`text-[9.5px] mt-1 ${
                  a.improvement_pts > 0 ? "text-hood-green" : a.improvement_pts < 0 ? "text-red-400" : "text-white/35"}`}>
                  {a.improvement_pts > 0 ? "▲" : a.improvement_pts < 0 ? "▼" : "•"}{" "}
                  {Math.abs(a.improvement_pts).toFixed(0)} pts last {a.recent.window}
                </div>
              )}
              {!!a.blamed_losses && (
                <div className="text-[9.5px] text-red-400/80 mt-0.5">
                  ⚠ {a.blamed_losses} backed loss{a.blamed_losses > 1 ? "es" : ""}
                </div>
              )}
            </motion.button>
          );
        })}
      </div>

      <AnimatePresence>
        {open && (() => {
          const a = data.find((x) => x.id === open)!;
          return (
            <motion.div key={a.id}
              initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden">
              <div className="mt-4 pt-4 border-t border-white/[0.08] grid grid-cols-1 md:grid-cols-3 gap-5">
                <Column title="Past · what the record says">
                  <Line k="Calls scored" v={`${a.samples}`} />
                  <Line k="Hit rate" v={pct(a.hit_rate)} />
                  <Line k="Brier" v={a.brier?.toFixed(3) ?? "—"}
                        note="lower is better; 0.25 is always saying 50%" />
                  <Line k="Stated confidence" v={pct(a.mean_confidence)} />
                  <Line k="Over/under-confidence"
                        v={a.overconfidence == null ? "—" : `${a.overconfidence > 0 ? "+" : ""}${a.overconfidence}`}
                        tone={a.calibrated ? "text-hood-green" : "text-amber-400"} />
                  <Line k="Abstentions" v={`${a.abstentions}`} />
                </Column>

                <Column title="Present · where it lacked">
                  <Line k={`Last ${a.recent.window} debates`} v={pct(a.recent.hit_rate)} />
                  <Line k="Everything before" v={pct(a.prior.hit_rate)} />
                  <Line k="Improvement"
                        v={a.improvement_pts == null ? "—"
                           : `${a.improvement_pts > 0 ? "+" : ""}${a.improvement_pts} pts`}
                        tone={a.improvement_pts == null ? undefined
                          : a.improvement_pts >= 0 ? "text-hood-green" : "text-red-400"} />
                  <Line k="Losses it backed" v={`${a.blamed_losses}`}
                        tone={a.blamed_losses ? "text-red-400" : undefined} />
                  {a.top_failure && (
                    <Line k="Dominant failure"
                          v={a.top_failure.code.replace(/_/g, " ")}
                          note={`${a.top_failure.count}×`} />
                  )}
                  {a.failure_note && (
                    <div className="text-[10.5px] text-white/45 leading-relaxed mt-1">
                      {a.failure_note}
                    </div>
                  )}
                </Column>

                <Column title="Future · enforcement and the bar">
                  {a.enforced.applied.map((t) => (
                    <div key={t} className="flex gap-2 text-[10.5px] leading-relaxed">
                      <span className="text-hood-green">enforced</span>
                      <span className="text-white/55">{t}</span>
                    </div>
                  ))}
                  {a.enforced.flagged.map((t) => (
                    <div key={t} className="flex gap-2 text-[10.5px] leading-relaxed">
                      <span className="text-amber-400">flagged</span>
                      <span className="text-white/55">{t}</span>
                    </div>
                  ))}
                  <div className="text-[10.5px] text-white/45 leading-relaxed mt-1.5 pt-2
                                  border-t border-white/[0.07]">
                    Next trades: {a.target.note}
                    {a.target.gap != null && a.target.gap > 0 &&
                      ` (bar is ${a.target.required_hit_rate.toFixed(0)}%)`}
                  </div>
                  <div className="text-[10px] text-white/30 leading-relaxed mt-1">
                    {a.mandate}
                  </div>
                </Column>
              </div>
            </motion.div>
          );
        })()}
      </AnimatePresence>
    </GlassCard>
  );
}

function Column({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="text-[10px] uppercase tracking-[0.12em] text-white/35 mb-2">{title}</div>
      {children}
    </div>
  );
}

function Line({ k, v, note, tone }: { k: string; v: string; note?: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-[11px]">
      <span className="text-white/40">{k}{note && <span className="text-white/25"> · {note}</span>}</span>
      <span className={`font-mono ${tone ?? "text-white/80"}`}>{v}</span>
    </div>
  );
}
