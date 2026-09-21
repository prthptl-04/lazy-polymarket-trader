import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Avatar } from "./Avatar";
import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill } from "./primitives";
import { usePoll, type LatestDebate, NEAR
} from "../lib/api";

/**
 * The committee thinking out loud.
 *
 * Each bubble carries the seat's OWN call, not the committee's — a dissenter is
 * the most informative thing on the panel, and folding it into a consensus
 * summary hides exactly what you want to see.
 *
 * An abstention is styled as an error rather than as a quiet neutral. A seat
 * that failed did not weigh in, so the table was thinner than its seat count
 * suggests, and that should look wrong.
 */
export function AgentDialogue({ title = "Agent round table · deliberation" }: { title?: string }) {
  const { data: debate } = usePoll<LatestDebate>("/api/roundtable/latest", NEAR);
  const feed = useRef<HTMLDivElement>(null);

  // Follow the conversation, but only when the thesis changes — yanking the
  // scroll on every 5s poll would fight anyone reading an earlier bubble.
  useEffect(() => {
    feed.current?.scrollTo({ top: feed.current.scrollHeight, behavior: "smooth" });
  }, [debate?.thesis_id]);

  if (!debate) {
    return (
      <GlassCard className="p-4" inert>
        <PanelTitle>{title}</PanelTitle>
        <Empty title="The committee has not convened."
               hint="Seats deliberate once a cycle finds a candidate that survives the deterministic screens — those run first precisely so tokens are never spent on a name the arithmetic already rejected." />
      </GlassCard>
    );
  }

  const tone = (s: string): "good" | "bad" | "neutral" =>
    s === "bullish" ? "good" : s === "bearish" ? "bad" : "neutral";

  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={
        <div className="flex items-center gap-1.5">
          <Pill>{debate.symbol}</Pill>
          {debate.unanimous && <Pill tone="warn">unanimous</Pill>}
        </div>
      }>{title}</PanelTitle>

      <div ref={feed} className="space-y-2.5 max-h-[360px] overflow-y-auto pr-1 scroll-smooth">
        <AnimatePresence initial={false}>
          {debate.opinions.map((o, i) => (
            <motion.div key={o.seat_id}
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05 }}
              className="flex gap-2.5">
              <Avatar seed={o.seat_name} size={28}
                      className="w-7 h-7 shrink-0 mt-0.5 border border-white/12" />
              <div className={`relative flex-1 rounded-2xl rounded-tl-sm border px-3 py-2
                ${o.failed ? "abstention-error" : "border-white/[0.09] bg-white/[0.045]"}`}>
                {/* [Call] | [Confidence] | [Reasoning] | [Concerns] */}
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-semibold text-white/85">{o.seat_name}</span>
                  {o.failed
                    ? <Pill tone="bad">abstention error</Pill>
                    : <span className="flex items-center gap-1.5">
                        <Pill tone={tone(o.signal)}>{o.signal}</Pill>
                        <span className="font-mono text-[10px] text-white/35">{o.confidence.toFixed(0)}%</span>
                      </span>}
                </div>
                <div className="text-[11.5px] text-white/60 mt-1 leading-relaxed">
                  {o.failed ? (o.error ?? "no call returned") : o.reasoning}
                </div>
                {!!o.concerns?.length && (
                  <div className="text-[10.5px] text-amber-400/70 mt-1.5">⚠ {o.concerns[0]}</div>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {debate.consensus?.summary && (
        <div className="mt-3 pt-3 border-t border-white/[0.08] chair-block rounded-xl p-2.5">
          <div className="flex items-center gap-2 mb-1">
            <Avatar seed="Chair" size={22} className="w-[22px] h-[22px]" />
            <span className="text-[11px] font-semibold text-white/85">Chair · final synthesis</span>
            {debate.consensus.signal && <Pill tone={tone(debate.consensus.signal)}>
              {debate.consensus.signal} {debate.consensus.confidence?.toFixed(0)}%
            </Pill>}
          </div>
          <div className="text-[11.5px] text-white/60 leading-relaxed">{debate.consensus.summary}</div>
          {debate.consensus.dissent && (
            <div className="text-[11px] text-amber-400/80 mt-1.5 leading-relaxed">
              Surviving objection: {debate.consensus.dissent}
            </div>
          )}
        </div>
      )}
    </GlassCard>
  );
}
