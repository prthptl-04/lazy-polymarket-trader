import { motion } from "framer-motion";
import type { ReactNode } from "react";

export const money = (v: number | null | undefined) =>
  v == null ? "—" : `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const signed = (v: number | null | undefined) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${money(v)}`.replace("+$-", "-$");

/** Green/red always carries a SIGN as well as a colour: roughly 8% of men
 *  cannot separate the two, and this is a screen about money. */
export const toneOf = (v: number | null | undefined) =>
  v == null ? "text-white/50" : v > 0 ? "text-hood-green" : v < 0 ? "text-red-400" : "text-white/50";

export function Stat({ label, value, sub, tone }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-[0.09em] text-white/35">{label}</div>
      <div className={`font-mono text-[19px] font-semibold tracking-tight mt-0.5 ${tone ?? "text-white"}`}>
        {value}
      </div>
      {sub && <div className="text-[11px] text-white/40 mt-0.5">{sub}</div>}
    </div>
  );
}

/** Empty states say WHY. "No positions" and "no data provider attached" mean
 *  very different things to someone deciding whether to trust the screen. */
export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="py-8 text-center">
      <div className="text-[13px] text-white/35">{title}</div>
      {hint && <div className="text-[11px] text-white/25 mt-1.5 max-w-md mx-auto leading-relaxed">{hint}</div>}
    </div>
  );
}

export function Pill({ children, tone = "neutral" }: {
  children: ReactNode; tone?: "good" | "bad" | "warn" | "neutral";
}) {
  const map = {
    good: "bg-hood-green/15 text-hood-green border-hood-green/25",
    bad: "bg-red-500/15 text-red-400 border-red-500/25",
    warn: "bg-amber-400/15 text-amber-400 border-amber-400/25",
    neutral: "bg-white/[0.07] text-white/50 border-white/10",
  } as const;
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-md border ${map[tone]}`}>{children}</span>
  );
}

/** Animated SVG check, drawn rather than faded in. */
export function DrawnCheck({ checked }: { checked: boolean }) {
  return (
    <span className={`w-[18px] h-[18px] rounded-[6px] border flex items-center justify-center shrink-0
      ${checked ? "bg-hood-green/20 border-hood-green/50" : "bg-white/[0.04] border-white/15"}`}>
      {checked && (
        <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
          <motion.path d="M2 6.5L4.8 9.2L10 3.5" stroke="#00c805" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round"
            initial={{ pathLength: 0 }} animate={{ pathLength: 1 }}
            transition={{ duration: 0.35, ease: "easeOut" }} />
        </svg>
      )}
    </span>
  );
}
