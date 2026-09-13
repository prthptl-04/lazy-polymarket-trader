import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Avatar } from "./Avatar";
import { LogLine, PulseDot, Skeleton } from "./lab";
import { usePoll, type LatestDebate } from "../lib/api";

const clock = (t: number | null | undefined) =>
  t ? new Date(t * 1000).toLocaleTimeString(undefined,
      { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "--:--:--";

/**
 * The committee, as a terminal log.
 *
 * Skeletons resolve into each seat's text rather than the whole panel snapping
 * into place — a deliberation is seven model calls that take real seconds, and
 * the page should show that it is waiting on generation, not that it is empty.
 *
 * The skeletons are shown ONLY while a fetch has not yet returned. A skeleton
 * that plays over data we already have is decoration pretending to be latency.
 */
export function LabDeliberation({ live }: { live: boolean }) {
  const { data: debate, loaded } = usePoll<LatestDebate>("/api/roundtable/latest", 5000);
  const [revealed, setRevealed] = useState(0);
  const feed = useRef<HTMLDivElement>(null);
  const thesis = debate?.thesis_id;

  // Reveal seat by seat on a NEW thesis only, then hold. Re-running the reveal
  // on every poll would make a settled debate look like it is still arriving.
  useEffect(() => {
    if (!debate) return;
    setRevealed(0);
    let n = 0;
    const id = setInterval(() => {
      n += 1;
      setRevealed(n);
      if (n >= debate.opinions.length + 1) clearInterval(id);
    }, 260);
    return () => clearInterval(id);
  }, [thesis, debate?.opinions.length]);

  useEffect(() => {
    feed.current?.scrollTo({ top: feed.current.scrollHeight, behavior: "smooth" });
  }, [revealed]);

  if (!loaded) return <Skeleton lines={5} className="mt-1" />;

  if (!debate) {
    return (
      <div className="text-[11.5px] text-[var(--lab-muted)] leading-relaxed py-6 text-center">
        The committee has not convened.
        <div className="text-[10.5px] mt-1.5 max-w-sm mx-auto">
          Seats deliberate once a cycle finds a candidate that survives the deterministic
          screens — those run first so tokens are never spent on a name the arithmetic
          already rejected.
        </div>
      </div>
    );
  }

  const tone = (s: string | null) =>
    s === "bullish" ? "#34d399" : s === "bearish" ? "#fb7185" : "var(--lab-muted)";

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-2 mb-2 text-[10px] font-mono text-[var(--lab-muted)]">
        <PulseDot live={live} />
        <span className="uppercase tracking-wider">{debate.symbol}</span>
        <span className="opacity-60">· {debate.opinions.length} seats</span>
        {debate.unanimous && <span className="text-[var(--lab-amber)]">· unanimous</span>}
        <span className="ml-auto">{clock(debate.created)}</span>
      </div>

      <div ref={feed} className="flex-1 min-h-0 overflow-y-auto space-y-2.5 pr-1">
        <AnimatePresence initial={false}>
          {debate.opinions.map((o, i) => (
            <motion.div key={o.seat_id} initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }} className="flex gap-2.5">
              <Avatar seed={o.seat_name} size={26}
                      className="w-[26px] h-[26px] shrink-0 mt-0.5 border border-white/10" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[11px] font-semibold text-[var(--lab-ink)]">
                    {o.seat_name}
                  </span>
                  {i < revealed && (
                    o.failed
                      ? <span className="font-mono text-[9.5px] uppercase tracking-wider text-[#fb7185]">
                          abstention error
                        </span>
                      : <span className="font-mono text-[9.5px] uppercase tracking-wider"
                              style={{ color: tone(o.signal) }}>
                          {o.signal} · {o.confidence.toFixed(0)}%
                        </span>
                  )}
                </div>
                {i < revealed ? (
                  <>
                    <div className="text-[11.5px] text-[var(--lab-ink)]/72 leading-relaxed mt-0.5">
                      {o.failed ? (o.error ?? "no call returned") : o.reasoning}
                    </div>
                    {!!o.concerns?.length && (
                      <div className="text-[10.5px] text-[var(--lab-amber)]/80 mt-1">
                        ⚠ {o.concerns[0]}
                      </div>
                    )}
                  </>
                ) : <Skeleton lines={2} className="mt-1.5" />}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>

        {debate.consensus?.summary && (
          revealed > debate.opinions.length ? (
            <div className="rounded-xl border border-[var(--lab-amber)]/25 bg-[var(--lab-amber)]/[0.06]
                            px-3 py-2 mt-1">
              <LogLine at={clock(debate.created)} tag="chair" tone="var(--lab-amber)">
                {debate.consensus.summary}
              </LogLine>
              {debate.consensus.dissent && (
                <div className="text-[10.5px] text-[var(--lab-amber)]/80 mt-1.5 pl-[4.6rem]">
                  surviving objection: {debate.consensus.dissent}
                </div>
              )}
            </div>
          ) : <Skeleton lines={2} className="mt-2" />
        )}
      </div>
    </div>
  );
}
