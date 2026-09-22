import { GlassCard, PanelTitle } from "./GlassCard";
import { Pill } from "./primitives";
import { usePoll, NEAR, type Balance as B, type LatestDebate } from "../lib/api";

/**
 * The count the chair decided on, not the prose it read.
 *
 * Across 47 of 51 neutral verdicts the typical table was one directional seat,
 * NOBODY on the other side, and the rest with no view — and that read as a
 * committee-wide stand-aside. Opposition and absence of a view are different
 * facts, and the panel that showed only the transcript could not distinguish
 * them either.
 *
 * The weighted row appears only once seats have a record. While every seat sits
 * at 1.00x it would be the raw row printed twice, implying an adaptation that
 * has not happened.
 */
export function Balance({ balance }: { balance?: B | null }) {
  // Self-fetching when not handed one, so the panel can sit on a page without
  // the deliberation being threaded through it. `AgentDialogue`, which this
  // was first wired into, turned out to be imported by nothing.
  const { data } = usePoll<LatestDebate>("/api/roundtable/latest", NEAR);
  balance = balance ?? data?.balance;
  if (!balance) return null;
  const { raw, weighted, abstained, unopposed, weights_active } = balance;
  const total = raw.bullish + raw.bearish + raw.neutral;
  if (!total && !abstained) return null;

  const Row = ({ label, v, mono }: {
    label: string; v: { bullish: number; bearish: number; neutral: number }; mono?: boolean;
  }) => (
    <div className="flex items-center gap-3">
      <span className="text-[10px] uppercase tracking-wide text-white/35 w-[74px] shrink-0">
        {label}
      </span>
      <div className="flex-1 flex h-2 rounded-full overflow-hidden bg-white/[0.06]">
        {v.bullish > 0 && <div className="bg-hood-green" style={{ flex: v.bullish }} />}
        {v.bearish > 0 && <div className="bg-red-400" style={{ flex: v.bearish }} />}
        {v.neutral > 0 && <div className="bg-white/20" style={{ flex: v.neutral }} />}
      </div>
      <span className={`shrink-0 text-[10.5px] ${mono ? "font-mono" : ""} text-white/55`}>
        {mono ? v.bullish.toFixed(2) : v.bullish} / {mono ? v.bearish.toFixed(2) : v.bearish}
        {" / "}{mono ? v.neutral.toFixed(2) : v.neutral}
      </span>
    </div>
  );

  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={
        unopposed ? <Pill tone="warn">unopposed</Pill> : <Pill>contested</Pill>
      }>Balance of opinion · what the chair was shown</PanelTitle>

      <div className="space-y-2.5">
        <Row label="Headcount" v={raw} />
        {weights_active && <Row label="Weighted" v={weighted} mono />}
      </div>

      <div className="text-[10.5px] text-white/40 mt-3 leading-relaxed">
        {unopposed
          ? "No seat argued the other side. The remaining seats reported no view, which is not the same as disagreeing."
          : "Seats took opposing sides."}
        {abstained > 0 && ` ${abstained} abstained — errored, did not weigh in.`}
        {!weights_active &&
          " Every seat still votes at 1.00× — no record has been earned yet."}
      </div>
      <div className="text-[9.5px] text-white/25 mt-1.5">
        green bullish · red bearish · grey no view
      </div>
    </GlassCard>
  );
}
