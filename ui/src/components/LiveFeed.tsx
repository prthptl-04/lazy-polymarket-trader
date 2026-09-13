import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill, money, toneOf } from "./primitives";
import { usePoll, type Feed } from "../lib/api";

/**
 * Live quotes from the venue itself, four seconds apart.
 *
 * A quote that failed keeps its row and shows the reason. Dropping it would
 * make a broken feed indistinguishable from a flat book — the two call for
 * opposite reactions, and only one of them is "do nothing".
 *
 * Only held symbols appear: the quote that matters is the one an exit would
 * fill at, and streaming names the fund does not own is noise on a panel whose
 * whole job is to be scanned quickly.
 */
export function LiveFeed({ venue, title }: { venue: string; title: string }) {
  const { data, stale } = usePoll<Feed[]>(`/api/feeds?venue=${venue}`, 4000);
  return (
    <GlassCard className="p-5" inert>
      <PanelTitle right={stale ? <Pill tone="warn">stale</Pill> : <Pill tone="good">live · 4s</Pill>}>
        {title}
      </PanelTitle>
      {data?.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] min-w-[460px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-white/35">
                <th className="text-left pb-2">Symbol</th>
                <th className="text-right pb-2">Bid</th>
                <th className="text-right pb-2">Last</th>
                <th className="text-right pb-2">Ask</th>
                <th className="text-right pb-2">Spread</th>
                <th className="text-right pb-2">% vs entry</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {data.map((f) => (
                <tr key={f.symbol} className="border-t border-white/[0.06]">
                  <td className="py-1.5 font-sans font-semibold text-white/85">{f.symbol}</td>
                  {f.reason ? (
                    // Muted gold under the Gold theme, amber elsewhere: an
                    // unreachable quote is a warning, not a loss.
                    <td colSpan={5} className="text-right font-sans text-[11px] feed-error">
                      quote unavailable · {f.reason}
                    </td>
                  ) : (
                    <>
                      <td className="text-right text-white/45">{money(f.bid)}</td>
                      <td className="text-right text-white/85">{money(f.last)}</td>
                      <td className="text-right text-white/45">{money(f.ask)}</td>
                      <td className="text-right text-white/35">
                        {f.spread_bps == null ? "—" : `${f.spread_bps.toFixed(0)}bp`}
                      </td>
                      <td className={`text-right ${toneOf(f.change_pct)}`}>
                        {f.change_pct == null ? "—"
                          : `${f.change_pct > 0 ? "+" : ""}${f.change_pct.toFixed(2)}%`}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty title="No live feed."
               hint="Feeds follow open positions — the quote that matters is the one an exit would fill at, so nothing is streamed for a symbol the fund does not hold." />
      )}
    </GlassCard>
  );
}
