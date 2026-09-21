import { motion } from "framer-motion";
import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill } from "./primitives";
import { usePoll, SLOW, type Catalysts as Cat, type Feed } from "../lib/api";

/**
 * What is about to happen, and what the insiders did.
 *
 * The evidence the Catalyst seat reasons from, rendered from the SAME endpoint
 * the committee reads — so the panel and the table can never be looking at
 * different news.
 *
 * Polled at SLOW on purpose. News and Form 4s move on the scale of hours; the
 * server caches for five minutes because yfinance and SEC EDGAR are
 * rate-limited and a 1Hz panel would be a denial of service against the
 * sources the fund depends on.
 *
 * An unavailable feed renders its REASON rather than an empty list. "No
 * catalysts" and "the provider needs an API key" look identical on a blank
 * panel and mean opposite things.
 */
export function Catalysts({ symbol, assetClass = "equity" }: {
  symbol: string; assetClass?: string;
}) {
  const { data } = usePoll<Cat>(
    `/api/catalysts?symbol=${encodeURIComponent(symbol)}&asset_class=${assetClass}`, SLOW);

  const headlines = (data?.notes ?? []).filter((n) => n.startsWith("["));
  const insider = (data?.notes ?? []).find((n) => n.startsWith("Insiders:"));

  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={
        <div className="flex items-center gap-1.5">
          <Pill>{symbol}</Pill>
          {data && !data.available && <Pill tone="warn">unavailable</Pill>}
        </div>
      }>Catalysts · events, filings, insider flow</PanelTitle>

      {!data ? (
        <Empty title="Loading catalysts…" />
      ) : !data.available ? (
        <Empty title="No catalyst evidence this cycle." hint={data.reason} />
      ) : (
        <div className="space-y-3">
          {insider && (
            <div className={`rounded-xl border px-3 py-2 ${
              insider.includes("cluster buying")
                ? "border-hood-green/30 bg-hood-green/[0.06]"
                : "border-white/[0.09] bg-white/[0.03]"}`}>
              <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1">
                Insider flow · 90 days
              </div>
              <div className="text-[11.5px] text-white/70 leading-relaxed">{insider}</div>
            </div>
          )}

          <div>
            <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1.5">
              Headlines
            </div>
            <div className="space-y-1.5 max-h-[280px] overflow-y-auto pr-1">
              {headlines.length === 0 && (
                <div className="text-[11px] text-white/35">The tape is quiet for this name.</div>
              )}
              {headlines.map((line, i) => {
                // "[2026-09-20] Title (Source)" — split so the date can lead.
                const m = line.match(/^\[([^\]]*)\]\s*(.*?)\s*\(([^)]*)\)\s*$/);
                const [date, title, source] = m ? [m[1], m[2], m[3]] : ["", line, ""];
                return (
                  <motion.div key={i}
                    initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.03 }}
                    className="flex gap-2 items-baseline">
                    <span className="font-mono text-[9.5px] text-white/30 shrink-0 w-[70px]">
                      {date}
                    </span>
                    <span className="text-[11.5px] text-white/70 leading-snug flex-1">
                      {title}
                      {source && <span className="text-white/30 ml-1">· {source}</span>}
                    </span>
                  </motion.div>
                );
              })}
            </div>
          </div>

          {!!data.degraded?.length && (
            // Named, not hidden. A seat that cannot see a source must discount
            // rather than read the silence as "nothing happening".
            <div className="text-[10px] text-amber-400/70 leading-relaxed pt-1 border-t border-white/[0.08]">
              Unavailable this cycle: {data.degraded.join("; ")}
            </div>
          )}
        </div>
      )}
    </GlassCard>
  );
}

/**
 * The catalysts for whatever the table is currently arguing about.
 *
 * Follows the live debate when one is running, and otherwise falls back to the
 * first instrument on the watchlist — so the panel is about the name in front
 * of the committee rather than a hardcoded ticker.
 */
export function CatalystsInFocus({ venue }: { venue: string }) {
  const { data: live } = usePoll<{ symbol: string | null }>("/api/roundtable/live", SLOW);
  const { data: feeds } = usePoll<Feed[]>(`/api/feeds?venue=${venue}`, SLOW);

  const symbol = live?.symbol || feeds?.[0]?.symbol;
  if (!symbol) return null;
  // Crypto has no issuer and no Form 4s; the feed skips the filing call rather
  // than reporting it unavailable.
  const assetClass = /-USD$|^(BTC|ETH|SOL|DOGE)/.test(symbol) ? "crypto" : "equity";
  return <Catalysts symbol={symbol} assetClass={assetClass} />;
}
