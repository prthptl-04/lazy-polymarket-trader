import { type ReactNode } from "react";
import { motion } from "framer-motion";

/**
 * Simulation Lab primitives: bento modules, terminal readouts, and the two
 * animations that are actually load-bearing here — a skeleton that resolves
 * into generated text, and a status dot that breathes while the engine runs.
 *
 * Everything here is presentation. No component in this file invents a number
 * or hides an absent one; a module with nothing to show says so.
 */

/** One cell of the bento grid. `span` is in 12ths at xl. */
export function Bento({ span = 4, rows, className = "", children, title, right, mono = false }: {
  span?: number; rows?: number; className?: string; children: ReactNode;
  title?: ReactNode; right?: ReactNode; mono?: boolean;
}) {
  return (
    <section
      className={`lab-panel flex flex-col p-4 ${className}`}
      style={{ gridColumn: `span ${span} / span ${span}`,
               gridRow: rows ? `span ${rows} / span ${rows}` : undefined }}>
      {(title || right) && (
        <header className="flex items-baseline justify-between gap-3 mb-3">
          <h2 className="lab-title">{title}</h2>
          {right}
        </header>
      )}
      <div className={`flex-1 min-h-0 ${mono ? "font-mono" : ""}`}>{children}</div>
    </section>
  );
}

/** A readout. Numbers are monospace here because they are read as data, and a
 *  proportional font makes a column of them impossible to scan. */
export function Readout({ label, value, sub, tone, amber = false }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: string; amber?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="lab-label">{label}</div>
      <div className={`font-mono text-[19px] font-semibold tracking-tight mt-0.5 ${
        amber ? "text-[var(--lab-amber)]" : tone ?? "text-[var(--lab-ink)]"}`}>
        {value}
      </div>
      {sub && <div className="text-[10.5px] text-[var(--lab-muted)] mt-0.5">{sub}</div>}
    </div>
  );
}

/** Breathing status dot. Stops when nothing is running — a dot that pulses
 *  while the engine is stopped is a lie told once a second. */
export function PulseDot({ live, tone = "var(--lab-amber)" }: { live: boolean; tone?: string }) {
  return (
    <span className="relative inline-flex w-2 h-2 shrink-0">
      {live && (
        <span className="absolute inset-0 rounded-full lab-breathe"
              style={{ background: tone }} />
      )}
      <span className="relative w-2 h-2 rounded-full"
            style={{ background: live ? tone : "rgba(148,163,184,0.45)" }} />
    </span>
  );
}

/** Skeleton bars with a sweeping highlight — the model is thinking, and the
 *  page should look like it is waiting for a generation rather than broken. */
export function Skeleton({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  const widths = ["92%", "78%", "86%", "64%", "70%"];
  return (
    <div className={`space-y-2 ${className}`} aria-hidden>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="lab-skeleton h-2.5 rounded"
             style={{ width: widths[i % widths.length] }} />
      ))}
    </div>
  );
}

/** Terminal line: timestamp, tag, text. Used by the deliberation log. */
export function LogLine({ at, tag, tone = "var(--lab-muted)", children }: {
  at?: string; tag?: string; tone?: string; children: ReactNode;
}) {
  return (
    <div className="flex gap-2.5 text-[11.5px] leading-relaxed">
      {at && <span className="font-mono text-[10px] text-[var(--lab-muted)] shrink-0 pt-0.5">{at}</span>}
      {tag && (
        <span className="font-mono text-[10px] shrink-0 pt-0.5 uppercase tracking-wider"
              style={{ color: tone }}>{tag}</span>
      )}
      <span className="text-[var(--lab-ink)]/75 min-w-0">{children}</span>
    </div>
  );
}

/** Progress track. Amber, because on this page progress is simulated progress. */
export function LabProgress({ pct, label }: { pct: number; label?: string }) {
  return (
    <div>
      <div className="h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
        <motion.div className="h-full rounded-full lab-progress"
          initial={{ width: 0 }} animate={{ width: `${Math.max(1.5, Math.min(100, pct))}%` }}
          transition={{ duration: 0.7, ease: "easeOut" }} />
      </div>
      {label && <div className="text-[10px] text-[var(--lab-muted)] mt-1.5">{label}</div>}
    </div>
  );
}
