import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill, money, toneOf } from "./primitives";
import { usePoll, type Feed } from "../lib/api";

/**
 * Live quotes from the venue itself.
 *
 * A quote that failed is shown with its reason rather than dropped — a feed
 * that silently shortens looks exactly like a flat book, and those two states
 * call for opposite reactions.
 */
export function LiveFeed({ venue, title }: { venue: string; title: string }) {
  const { data, stale } = usePoll<Feed[]>(`/api/feeds?venue=${venue}`, 4000);
  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={stale ? <Pill tone="warn">stale</Pill> : <Pill tone="good">live</Pill>}>
        {title}
      </PanelTitle>
      {data?.length ? (
        <div className="space-y-1.5">
          {data.map((f) => (
            <div key={f.symbol}
                 className="flex items-center justify-between gap-3 rounded-xl
                            border border-white/[0.06] bg-white/[0.03] px-3 py-2">
              <span className="text-[12px] font-semibold text-white/85">{f.symbol}</span>
              {f.reason ? (
                <span className="text-[10.5px] text-amber-400/70">{f.reason}</span>
              ) : (
                <div className="flex items-center gap-3 font-mono text-[11.5px]">
                  <span className="text-white/40">{money(f.bid)}</span>
                  <span className="text-white/85">{money(f.last)}</span>
                  <span className="text-white/40">{money(f.ask)}</span>
                  {f.spread_bps != null &&
                    <span className="text-white/25">{f.spread_bps.toFixed(0)}bp</span>}
                  {f.change_pct != null && (
                    <span className={`${toneOf(f.change_pct)} w-14 text-right`}>
                      {f.change_pct > 0 ? "+" : ""}{f.change_pct.toFixed(2)}%
                    </span>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <Empty title="No live feed."
               hint="Feeds follow open positions — the quote that matters is the one an exit would fill at, so nothing is streamed for a symbol the fund does not hold." />
      )}
    </GlassCard>
  );
}
