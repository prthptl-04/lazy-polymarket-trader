import { GlassCard, PanelTitle } from "./GlassCard";
import { Pill } from "./primitives";
import { PriceChart } from "./PriceChart";
import { LIVE, NEAR, usePoll, type Deliberation, type Feed } from "../lib/api";

/**
 * What the committee is looking at, priced live.
 *
 * The page could show the fund's equity curve and nothing else, which is a
 * single point until something closes — so every chart read as broken while
 * the prices the fund was actually reasoning about went undrawn.
 *
 * The watchlist IS the story here: these are the instruments under debate right
 * now, so each one carries its own chart and, where the committee has reached a
 * view, the view. A symbol with an open deliberation is marked, because
 * "watching" and "arguing about" are different states and the difference is the
 * interesting part.
 */
export function LivePrices({ venue = "robinhood" }: { venue?: string }) {
  const { data: feed } = usePoll<Feed[]>(`/api/feeds?venue=${venue}`, LIVE);
  const { data: debates } = usePoll<Deliberation[]>("/api/deliberations?limit=25", NEAR);

  const rows = feed ?? [];
  const latest = new Map<string, Deliberation>();
  for (const d of debates ?? []) if (!latest.has(d.symbol)) latest.set(d.symbol, d);

  if (!rows.length) {
    return (
      <GlassCard className="p-5" inert>
        <PanelTitle>Live prices · under discussion</PanelTitle>
        <div className="text-[11px] text-white/40 py-4">
          Nothing is being watched. Set a watchlist — FUND_CRYPTO_WATCHLIST for
          weekends, or let the scout screen the tape on a weekday.
        </div>
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-5" inert>
      <PanelTitle right={<Pill>{rows.length} watched</Pill>}>
        Live prices · under discussion
      </PanelTitle>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mt-1">
        {rows.map(row => {
          const debate = latest.get(row.symbol);
          const call = debate?.signal;
          const tone = call === "bullish" ? "good" : call === "bearish" ? "bad" : "neutral";
          return (
            <div key={row.symbol} className="venue-inset -mx-1 p-3 rounded-2xl">
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span className="text-[12px] font-semibold text-white/85">{row.symbol}</span>
                  {row.held
                    ? <Pill tone="good">held</Pill>
                    : <Pill>watching</Pill>}
                </div>
                {/* The spread is the number that decides whether this instrument
                    rests or is refused outright, so it belongs on the face of
                    the panel rather than buried in a deliberation. */}
                {row.spread_bps != null && (
                  <span className="font-mono text-[10px] text-white/35">
                    {row.spread_bps} bps spread
                  </span>
                )}
              </div>

              <PriceChart symbol={row.symbol} height={170} />

              <div className="flex items-center justify-between mt-2 text-[10.5px]">
                <span className="text-white/35 font-mono">
                  {row.bid != null && row.ask != null
                    ? `${row.bid.toLocaleString(undefined, { maximumFractionDigits: 2 })} / ${row.ask.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                    : row.reason ?? "no quote"}
                </span>
                {debate
                  ? <span className="flex items-center gap-1.5">
                      <Pill tone={tone as any}>{call ?? "deliberating"}</Pill>
                      {debate.confidence != null && (
                        <span className="font-mono text-white/35">
                          {debate.confidence.toFixed(0)}%
                        </span>
                      )}
                    </span>
                  : <span className="text-white/25">not yet debated</span>}
              </div>
            </div>
          );
        })}
      </div>
    </GlassCard>
  );
}
