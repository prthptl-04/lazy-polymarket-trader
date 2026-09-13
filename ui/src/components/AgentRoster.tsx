import { motion } from "framer-motion";
import { Avatar } from "./Avatar";
import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill } from "./primitives";
import { usePoll, type AgentMatrixRow } from "../lib/api";

/**
 * The roster, best first.
 *
 * Ranked on hit rate, with Brier breaking ties — two seats at 60% are not
 * equally good if one of them says 60% and the other says 95%. Unscored seats
 * sort last rather than to the top or the middle: a seat with no record is not
 * performing well, it is untested, and either of the other placements would
 * claim otherwise.
 */
export function AgentRoster() {
  const { data } = usePoll<AgentMatrixRow[]>("/api/agents/matrix", 20000);

  const ranked = [...(data ?? [])].sort((a, b) => {
    if (!a.samples !== !b.samples) return a.samples ? -1 : 1;   // scored first
    if (!a.samples && !b.samples) return a.name.localeCompare(b.name);
    const hit = (b.hit_rate ?? 0) - (a.hit_rate ?? 0);
    return hit !== 0 ? hit : (a.brier ?? 1) - (b.brier ?? 1);
  });

  return (
    <GlassCard className="md:col-span-2 p-5" inert>
      <PanelTitle right={<Pill>best first</Pill>}>
        Roster · who is carrying the table
      </PanelTitle>
      {ranked.length ? (
        <div className="space-y-1.5">
          {ranked.map((a, i) => {
            const good = a.hit_rate != null && a.hit_rate >= 50;
            return (
              <motion.div key={a.id}
                initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.04 }}
                className="flex items-center gap-3 rounded-xl border border-white/[0.07]
                           bg-white/[0.03] px-3 py-2">
                <span className="w-5 font-mono text-[11px] text-white/30 text-right">
                  {a.samples ? i + 1 : "—"}
                </span>
                <Avatar seed={a.name} size={38} className="w-9 h-9 border border-white/12 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="text-[12px] font-semibold text-white/85 leading-tight">
                    {a.name}
                  </div>
                  {/* The duty, not a label: which seat to believe about what. */}
                  <div className="text-[10.5px] text-white/40 leading-snug truncate">
                    {a.mandate}
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className={`font-mono text-[14px] ${
                    !a.samples ? "text-white/25" : good ? "text-hood-green" : "text-red-400"}`}>
                    {a.hit_rate == null ? "—" : `${a.hit_rate.toFixed(0)}%`}
                  </div>
                  <div className="text-[9px] text-white/30">
                    {a.samples ? `${a.samples} calls` : "unscored"}
                  </div>
                </div>
                {a.improvement_pts != null && (
                  <span className={`w-12 text-right font-mono text-[10px] ${
                    a.improvement_pts > 0 ? "text-hood-green"
                      : a.improvement_pts < 0 ? "text-red-400" : "text-white/30"}`}>
                    {a.improvement_pts > 0 ? "▲" : a.improvement_pts < 0 ? "▼" : "•"}
                    {Math.abs(a.improvement_pts).toFixed(0)}
                  </span>
                )}
              </motion.div>
            );
          })}
        </div>
      ) : <Empty title="No roster." />}
      <div className="text-[10px] text-white/30 mt-3">
        Ranked on hit rate, Brier breaking ties. Unscored seats sort last — untested is not the same as good.
      </div>
    </GlassCard>
  );
}
