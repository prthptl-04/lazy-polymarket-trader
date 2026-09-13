import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { DrawnCheck } from "./primitives";

export interface Step { label: string; ok: boolean; detail?: string; required?: boolean }

/**
 * The checks, run in front of you.
 *
 * Steps reveal one at a time so the sequence is legible rather than a list that
 * appears all at once. A failing REQUIRED step halts the run and stays on
 * screen with its reason — the action never fires, and you are told which check
 * stopped it rather than watching a button quietly do nothing.
 */
export function Preflight({ title, steps, onComplete, onDismiss }: {
  title: string; steps: Step[]; onComplete: () => void; onDismiss: () => void;
}) {
  const [shown, setShown] = useState(0);
  const [done, setDone] = useState(false);

  const blockedAt = steps.findIndex((s) => s.required !== false && !s.ok);

  useEffect(() => {
    if (shown >= steps.length) return;
    // Stop advancing once a required check has failed.
    if (blockedAt >= 0 && shown > blockedAt) return;
    const id = setTimeout(() => setShown((n) => n + 1), shown === 0 ? 120 : 420);
    return () => clearTimeout(id);
  }, [shown, steps.length, blockedAt]);

  useEffect(() => {
    if (shown < steps.length || done) return;
    setDone(true);
    if (blockedAt < 0) {
      onComplete();
      const id = setTimeout(onDismiss, 2200);
      return () => clearTimeout(id);
    }
  }, [shown, steps.length, blockedAt, done, onComplete, onDismiss]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 12, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 8, scale: 0.98 }}
      className="fixed bottom-6 right-6 z-[110] w-[330px] rounded-2xl border border-white/12
                 bg-[#0c1016]/95 backdrop-blur-xl p-4 shadow-glass">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-white/60">
          {title}
        </span>
        <button onClick={onDismiss} aria-label="dismiss"
                className="text-white/30 hover:text-white/70 text-[13px] leading-none">×</button>
      </div>

      <div className="space-y-2">
        <AnimatePresence initial={false}>
          {steps.slice(0, shown).map((s, i) => (
            <motion.div key={s.label}
              initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
              className="flex items-start gap-2.5">
              {s.ok
                ? <DrawnCheck checked />
                : <span className="w-[18px] h-[18px] rounded-[6px] border border-red-400/50
                                   bg-red-500/15 text-red-400 text-[11px] leading-[16px]
                                   text-center shrink-0">✕</span>}
              <div className="min-w-0">
                <div className={`text-[11.5px] ${s.ok ? "text-white/75" : "text-red-400"}`}>
                  {s.label}
                </div>
                {s.detail && (
                  <div className="text-[10.5px] text-white/35 leading-snug mt-0.5">{s.detail}</div>
                )}
              </div>
              {i === shown - 1 && !done && blockedAt < 0 && (
                <motion.span className="ml-auto w-1.5 h-1.5 rounded-full bg-white/40 mt-1.5"
                  animate={{ opacity: [0.25, 1, 0.25] }}
                  transition={{ duration: 1, repeat: Infinity }} />
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {done && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          className={`text-[11px] mt-3 pt-3 border-t border-white/[0.08] ${
            blockedAt < 0 ? "text-hood-green" : "text-red-400"}`}>
          {blockedAt < 0 ? "Running." : `Halted — ${steps[blockedAt].label.toLowerCase()}.`}
        </motion.div>
      )}
    </motion.div>
  );
}
