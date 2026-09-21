import { AnimatePresence, motion } from "framer-motion";
import { Avatar } from "./Avatar";
import { GlassCard, PanelTitle } from "./GlassCard";
import { Pill } from "./primitives";
import { LIVE, usePoll } from "../lib/api";

/**
 * The committee mid-thought.
 *
 * A deliberation is seven LLM calls over 30-60 seconds, and the panels showed
 * the PREVIOUS thesis for all of it and then jumped. The reasoning arrives long
 * before the conclusion and is the more interesting half.
 *
 * Two things this must not imply. It shows `answered / expected` rather than a
 * bare count, because two of six reads very differently from two of two — and
 * it never renders a consensus while the debate is running, since four seats in
 * is not a decision and a panel suggesting otherwise is worse than no panel.
 */

interface LiveOpinion {
  seat_id: string; seat_name: string; signal: string | null;
  confidence: number | null; reasoning: string | null;
  failed: boolean; error: string | null;
}
interface LiveDebateData {
  symbol: string | null; opinions: LiveOpinion[];
  answered: number; expected_seats: number; in_progress: boolean;
}

export function LiveDebate({ title = "Committee · thinking now" }: { title?: string }) {
  const { data } = usePoll<LiveDebateData>("/api/roundtable/live", LIVE);
  const answered = data?.answered ?? 0;
  const expected = data?.expected_seats ?? 6;
  const running = !!data?.in_progress;

  if (!data?.symbol) {
    return (
      <GlassCard className="p-4" inert>
        <PanelTitle>{title}</PanelTitle>
        <div className="text-[11px] text-white/40 py-3 leading-relaxed">
          No committee sitting. Seats convene once a cycle finds a candidate the
          deterministic screens have not already rejected — those run first
          precisely so tokens are never spent on a name the arithmetic refused.
        </div>
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={
        <div className="flex items-center gap-1.5">
          <Pill>{data.symbol}</Pill>
          <Pill tone={running ? "warn" : "neutral"}>
            {answered}/{expected} {running ? "answering" : "done"}
          </Pill>
        </div>
      }>{title}</PanelTitle>

      {running && (
        <div className="h-1 rounded-full bg-white/[0.07] overflow-hidden mb-3">
          <motion.div className="h-full rounded-full bg-amber-400/70"
            initial={{ width: 0 }}
            animate={{ width: `${Math.max(4, (answered / expected) * 100)}%` }}
            transition={{ duration: 0.4, ease: "easeOut" }} />
        </div>
      )}

      <div className="space-y-2">
        <AnimatePresence initial={false}>
          {data.opinions.map(o => (
            <motion.div key={o.seat_id}
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              className="flex gap-2.5">
              <Avatar seed={o.seat_name} size={26}
                      className="w-[26px] h-[26px] shrink-0 mt-0.5 border border-white/12" />
              <div className={`flex-1 rounded-2xl rounded-tl-sm border px-3 py-2 ${
                o.failed ? "abstention-error" : "border-white/[0.09] bg-white/[0.045]"}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-semibold text-white/85">{o.seat_name}</span>
                  {o.failed
                    ? <Pill tone="bad">abstained</Pill>
                    : <span className="flex items-center gap-1.5">
                        <Pill tone={o.signal === "bullish" ? "good"
                                  : o.signal === "bearish" ? "bad" : "neutral"}>
                          {o.signal}
                        </Pill>
                        {o.confidence != null && (
                          <span className="font-mono text-[10px] text-white/35">
                            {o.confidence.toFixed(0)}%
                          </span>
                        )}
                      </span>}
                </div>
                <div className="text-[11.5px] text-white/60 mt-1 leading-relaxed">
                  {o.failed ? (o.error ?? "no call returned") : o.reasoning}
                </div>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>

        {/* Seats still out. Showing the gap is what makes the panel read as a
            debate in progress rather than as a finished, thin one. */}
        {running && Array.from({ length: Math.max(0, expected - answered) }).map((_, i) => (
          <div key={`pending-${i}`} className="flex gap-2.5 opacity-40">
            <div className="w-[26px] h-[26px] shrink-0 mt-0.5 rounded-full
                            border border-white/10 bg-white/[0.04]" />
            <div className="flex-1 rounded-2xl rounded-tl-sm border border-white/[0.06]
                            bg-white/[0.02] px-3 py-2">
              <motion.div className="h-2 rounded bg-white/10 w-1/3"
                animate={{ opacity: [0.3, 0.7, 0.3] }}
                transition={{ duration: 1.4, repeat: Infinity, delay: i * 0.15 }} />
            </div>
          </div>
        ))}
      </div>
    </GlassCard>
  );
}
