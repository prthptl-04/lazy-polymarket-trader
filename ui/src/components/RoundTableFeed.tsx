import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill } from "./primitives";
import { usePoll, type Deliberation } from "../lib/api";

/** What makes a decision worth marking. Unanimity is a caution flag, not a
 *  green light — the Devil's Advocate seat exists because a table that agrees
 *  with itself has stopped testing the thesis. */
function critical(d: Deliberation, dissent: boolean):
  { label: string; tone: "good" | "bad" | "warn" } | null {
  if (!dissent) return { label: "unanimous", tone: "warn" };
  if ((d.confidence ?? 0) >= 80) return { label: "high conviction", tone: "good" };
  if (d.status === "halted") return { label: "halted", tone: "bad" };
  return null;
}

/** Live committee feed. Each row is a real deliberation from the memory store;
 *  critical decisions carry a marker on the rail so you can scroll the history
 *  and find the moments that mattered without reading every row. */
export function RoundTableFeed({ title = "Live agent round table", max = 330 }: {
  title?: string; max?: number;
}) {
  const { data } = usePoll<Deliberation[]>("/api/deliberations?limit=25", 6000);
  return (
    <GlassCard className="p-4 flex flex-col" inert>
      <PanelTitle right={<Pill>{data?.length ?? 0}</Pill>}>{title}</PanelTitle>
      <div className="flex-1 overflow-y-auto space-y-2 pr-1" style={{ maxHeight: max }}>
        {data?.length ? data.map((d) => {
          const dissent = Object.values(d.tally ?? {}).filter((n) => n > 0).length > 1;
          const mark = critical(d, dissent);
          return (
            <a key={d.thesis_id} href={`/roundtable#${d.thesis_id}`}
               className="relative block rounded-xl border border-white/[0.07] bg-white/[0.03]
                          pl-4 pr-3 py-2 hover:bg-white/[0.06] transition-colors">
              {/* Rail marker — a bar, not a dot, so it survives a fast scroll. */}
              {mark && (
                <span className={`absolute left-0 top-2 bottom-2 w-[3px] rounded-full ${
                  mark.tone === "bad" ? "bg-red-400"
                    : mark.tone === "warn" ? "bg-amber-400" : "bg-hood-green"}`} />
              )}
              <div className="flex items-center justify-between gap-2">
                <span className="text-[12px] font-semibold text-white/85">{d.symbol}</span>
                <Pill tone={d.signal === "bullish" ? "good" : d.signal === "bearish" ? "bad" : "neutral"}>
                  {d.signal ?? d.status}
                </Pill>
              </div>
              <div className="flex items-center gap-3 mt-1 text-[10px] text-white/40">
                <span>{d.seats} seats</span>
                {d.confidence != null && <span>conf {d.confidence.toFixed(0)}</span>}
                <span className={dissent ? "text-white/40" : "text-amber-400/80"}>
                  {dissent ? "dissent" : "unanimous"}
                </span>
                {mark && <span className="ml-auto text-white/30">{mark.label}</span>}
              </div>
            </a>
          );
        }) : (
          <Empty title="No deliberations yet."
                 hint="The committee convenes once a cycle finds a candidate that survives the deterministic screens." />
        )}
      </div>
    </GlassCard>
  );
}
