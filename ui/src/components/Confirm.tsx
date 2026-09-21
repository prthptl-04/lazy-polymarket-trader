import { AnimatePresence, motion } from "framer-motion";
import { useEffect } from "react";

/**
 * A decision that is hard to walk back gets a sentence and two buttons.
 *
 * Escape and the backdrop both cancel; only the primary button confirms. The
 * cancel path is the safe one, so it is the one every accidental input takes.
 */
export function Confirm({ open, title, body, confirmLabel = "Start", tone = "good", onConfirm, onCancel }: {
  open: boolean; title: string; body: React.ReactNode; confirmLabel?: string;
  tone?: "good" | "bad"; onConfirm: () => void; onCancel: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-[100] flex items-center justify-center p-6"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-black/65 backdrop-blur-sm" onClick={onCancel} />
          <motion.div role="dialog" aria-modal="true" aria-label={title}
            initial={{ scale: 0.96, y: 8 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.97, opacity: 0 }}
            transition={{ type: "spring", stiffness: 420, damping: 32 }}
            className="relative w-full max-w-[420px] rounded-3xl border border-white/12
                       bg-[#0c1016]/95 p-6 shadow-glass">
            <h2 className="text-[15px] font-semibold text-white">{title}</h2>
            {/* Full white, not a muted grey. This dialog is the last thing read
                before the fund starts trading, and the sentence explaining what
                paper mode does is the one sentence that must not be skimmed. */}
            <div className="text-[12px] text-white mt-2 leading-relaxed">{body}</div>
            <div className="flex gap-2 mt-5">
              <button onClick={onCancel}
                className="flex-1 py-2 rounded-xl text-[12px] font-medium
                           border border-white/15 text-white/70 hover:bg-white/[0.06]">
                Cancel
              </button>
              <button onClick={onConfirm} autoFocus
                className={`flex-1 py-2 rounded-xl text-[12px] font-bold ${
                  tone === "good" ? "bg-gradient-to-r from-green-400 to-green-600 text-black"
                                  : "bg-gradient-to-r from-red-500 to-red-700 text-white"}`}>
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
