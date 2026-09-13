import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill } from "./primitives";
import { usePoll, type Deliberation } from "../lib/api";

/** Live committee feed. Each row is a real deliberation from the memory store;
 *  the tally shows whether the seats actually disagreed, because a unanimous
 *  table is treated as a caution flag rather than a green light. */
export function RoundTableFeed({ title = "Live agent round table" }: { title?: string }) {
  const { data } = usePoll<Deliberation[]>("/api/deliberations?limit=12", 6000);
  return (
    <GlassCard className="p-4 flex flex-col" inert>
      <PanelTitle>{title}</PanelTitle>
      <div className="flex-1 overflow-y-auto space-y-2 max-h-[330px] pr-1">
        {data?.length ? data.map((d) => {
          const dissent = Object.values(d.tally ?? {}).filter((n) => n > 0).length > 1;
          return (
            <a key={d.thesis_id} href={`/roundtable#${d.thesis_id}`}
               className="block rounded-xl border border-white/[0.07] bg-white/[0.03] px-3 py-2
                          hover:bg-white/[0.06] transition-colors">
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
