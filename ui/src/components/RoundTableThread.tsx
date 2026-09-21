import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { Avatar } from "./Avatar";
import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill } from "./primitives";
import { usePoll, type ThreadMessage, NEAR
} from "../lib/api";

const clock = (t: number | null) =>
  t ? new Date(t * 1000).toLocaleTimeString(undefined,
      { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";

/**
 * The room, as one running conversation.
 *
 * The paper rail's dialogue panel answers "what is the committee saying about
 * this candidate". This answers "what has the committee been saying" — several
 * debates flattened oldest-first, so a symbol change reads as the thread moving
 * on rather than as the panel being replaced.
 *
 * It follows new messages only when the tail is already in view. Yanking the
 * scroll while someone is reading an earlier exchange is how a live feed
 * becomes unreadable.
 */
export function RoundTableThread({ title = "Live round table · discussion" }: { title?: string }) {
  const { data } = usePoll<ThreadMessage[]>("/api/roundtable/thread?limit=6", NEAR);
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const el = box.current;
    if (!el || !pinned.current) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [data?.length]);

  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  const tone = (s: string | null): "good" | "bad" | "neutral" =>
    s === "bullish" ? "good" : s === "bearish" ? "bad" : "neutral";

  return (
    <GlassCard className="p-5" inert>
      <PanelTitle right={<Pill>{data?.length ?? 0} messages</Pill>}>{title}</PanelTitle>
      {data?.length ? (
        <div ref={box} onScroll={onScroll}
             className="space-y-3 max-h-[420px] overflow-y-auto pr-1">
          {data.map((m, i) => {
            const newThesis = i === 0 || data[i - 1].thesis_id !== m.thesis_id;
            return (
              <div key={`${m.thesis_id}-${m.seat_id}-${i}`}>
                {newThesis && (
                  <div className="flex items-center gap-2 my-2">
                    <span className="h-px flex-1 bg-white/[0.08]" />
                    <span className="text-[10px] uppercase tracking-[0.12em] text-white/35">
                      {m.symbol}
                    </span>
                    <span className="h-px flex-1 bg-white/[0.08]" />
                  </div>
                )}
                <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
                            className="flex gap-3">
                  <div className="flex flex-col items-center w-14 shrink-0">
                    <Avatar seed={m.seat_name} size={36}
                            className="w-9 h-9 border border-white/12" />
                    {/* Name under the face, as asked — the ring teaches position,
                        the thread teaches who is talking. */}
                    <span className="w-full truncate text-[8.5px] text-white/45
                                     text-center leading-tight mt-1">
                      {m.seat_name.split(" ")[0]}
                    </span>
                  </div>
                  <div className={`flex-1 rounded-2xl rounded-tl-sm border px-3 py-2 ${
                    m.role === "chair" ? "chair-block border-white/[0.10]"
                      : m.failed ? "abstention-error"
                      : "border-white/[0.09] bg-white/[0.04]"}`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[11.5px] font-semibold text-white/85">
                        {m.role === "chair" ? "Chair · synthesis" : m.seat_name}
                      </span>
                      <span className="flex items-center gap-1.5">
                        {m.failed
                          ? <Pill tone="bad">abstention error</Pill>
                          : m.signal && <Pill tone={tone(m.signal)}>
                              {m.signal}{m.confidence != null && ` ${m.confidence.toFixed(0)}%`}
                            </Pill>}
                        <span className="font-mono text-[9.5px] text-white/25">{clock(m.created)}</span>
                      </span>
                    </div>
                    <div className="text-[11.5px] text-white/60 mt-1 leading-relaxed">
                      {m.failed ? (m.error ?? "no call returned") : m.reasoning}
                    </div>
                    {!!m.concerns.length && (
                      <div className="text-[10.5px] text-amber-400/70 mt-1.5">⚠ {m.concerns[0]}</div>
                    )}
                  </div>
                </motion.div>
              </div>
            );
          })}
        </div>
      ) : (
        <Empty title="The room is quiet."
               hint="Seats speak once a cycle finds a candidate that survives the deterministic screens — those run first so tokens are never spent on a name the arithmetic already rejected." />
      )}
    </GlassCard>
  );
}
