import { AnimatePresence, motion } from "framer-motion";
import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill } from "./primitives";
import { usePoll, type Agent, type LatestDebate } from "../lib/api";

/**
 * The committee thinking out loud.
 *
 * Each seat gets a speech bubble carrying its OWN call and reasoning, not the
 * committee's — a seat that dissented is the most informative thing on the
 * panel, and folding it into a consensus summary would hide exactly what you
 * want to see.
 */
export function AgentDialogue({ title = "Agent round table · deliberation" }: { title?: string }) {
  const { data: debate } = usePoll<LatestDebate>("/api/roundtable/latest", 5000);
  const { data: agents } = usePoll<Agent[]>("/api/agents", 60000);
  const icons = Object.fromEntries((agents ?? []).map((a) => [a.id, a.icon]));

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

      <div className="space-y-2.5 max-h-[340px] overflow-y-auto pr-1">
        <AnimatePresence initial={false}>
          {debate.opinions.map((o, i) => (
            <motion.div key={o.seat_id}
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05 }}
              className="flex gap-2.5">
              <div className="w-7 h-7 rounded-full bg-white/[0.07] border border-white/12
                              flex items-center justify-center text-[13px] shrink-0 mt-0.5">
                {icons[o.seat_id] ?? "•"}
              </div>
              {/* Thinking box: a bubble with a notch, so it reads as speech
                  rather than as a log line. */}
              <div className="relative flex-1 rounded-2xl rounded-tl-sm border border-white/[0.09]
                              bg-white/[0.045] px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-semibold text-white/85">{o.seat_name}</span>
                  {o.failed
                    ? <Pill tone="warn">abstained</Pill>
                    : <span className="flex items-center gap-1.5">
                        <Pill tone={tone(o.signal)}>{o.signal}</Pill>
                        <span className="font-mono text-[10px] text-white/35">{o.confidence.toFixed(0)}</span>
                      </span>}
                </div>
                <div className="text-[11.5px] text-white/60 mt-1 leading-relaxed">
                  {o.failed ? (o.error ?? "unavailable") : o.reasoning}
                </div>
                {!!o.concerns?.length && (
                  <div className="text-[10.5px] text-amber-400/70 mt-1.5">
                    ⚠ {o.concerns[0]}
                  </div>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {debate.consensus?.summary && (
        <div className="mt-3 pt-3 border-t border-white/[0.08]">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[13px]">🏛</span>
            <span className="text-[11px] font-semibold text-white/80">Chair</span>
            {debate.consensus.signal && <Pill tone={tone(debate.consensus.signal)}>
              {debate.consensus.signal} {debate.consensus.confidence?.toFixed(0)}
            </Pill>}
          </div>
          <div className="text-[11.5px] text-white/55 leading-relaxed">{debate.consensus.summary}</div>
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
