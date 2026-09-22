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

  // Split on shape, not on a list of known prefixes. The previous version
  // matched "[" and "Insiders:" and silently dropped five other note types —
  // earnings, filings, order book, implied move and disclosed official trades —
  // so the committee was reading evidence the operator could not see.
  const notes = data?.notes ?? [];
  const headlines = notes.filter((n) => n.startsWith("["));
  const facts = notes.filter((n) => !n.startsWith("["));

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
          {facts.map((note, i) => {
            // "Earnings: COST reports in 3 days…" -> label + body.
            const cut = note.indexOf(":");
            const label = cut > 0 && cut < 34 ? note.slice(0, cut) : "Note";
            const body = cut > 0 && cut < 34 ? note.slice(cut + 1).trim() : note;
            const good = /cluster buying|cluster/i.test(note);
            // The lines that change a decision rather than colour one in.
            const warn = /wider than the stop|reports (TODAY|in [0-3] days)|NO RESTING/i
              .test(note);
            return (
              <div key={i} className={`rounded-xl border px-3 py-2 ${
                warn ? "border-amber-400/30 bg-amber-400/[0.06]"
                     : good ? "border-hood-green/30 bg-hood-green/[0.06]"
                            : "border-white/[0.09] bg-white/[0.03]"}`}>
                <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1">
                  {label}
                </div>
                <div className="text-[11.5px] text-white/70 leading-relaxed">{body}</div>
              </div>
            );
          })}

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
                // Sanitiser output. A source that tried an override is a fact
                // about that source, so it is shown — and shown as suspect.
                const flagged = title.includes("[FLAGGED");
                return (
                  <motion.div key={i}
                    initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.03 }}
                    className="flex gap-2 items-baseline">
                    <span className="font-mono text-[9.5px] text-white/30 shrink-0 w-[70px]">
                      {date}
                    </span>
                    <span className={`text-[11.5px] leading-snug flex-1 ${
                      flagged ? "text-amber-400/80" : "text-white/70"}`}>
                      {flagged && <span className="mr-1" title="possible prompt injection">⚠</span>}
                      {title.replace("[FLAGGED: possible prompt injection in this source]", "").trim()}
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
