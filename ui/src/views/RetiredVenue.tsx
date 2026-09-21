import { useEffect } from "react";
import { motion } from "framer-motion";
import { GlassCard, PanelTitle } from "../components/GlassCard";
import { Pill } from "../components/primitives";
import { usePoll, type EngineState, NEAR
} from "../lib/api";

/**
 * A venue the fund no longer trades.
 *
 * The page stays in the tab bar on purpose. Deleting it would make the
 * decision invisible — an operator who remembers a Polymarket tab and cannot
 * find it assumes a bug, goes looking for the regression, and finds nothing.
 * A page that says "retired, here is why, here is what replaced it" answers
 * the question before it is asked.
 *
 * Everything live is stripped out: no polling of positions or feeds, no
 * engine switch, no GO button. The only network call is the one that reads
 * the retirement reason back from the server, so this page cannot drift out
 * of step with `trading/venues/retired.py` — the backend stays the single
 * source of truth for *why*.
 */
export function RetiredVenue({ venue, title }: { venue: string; title: string }) {
  const { data } = usePoll<{ engines: Record<string, EngineState> }>("/api/engines", NEAR);
  const engine = data?.engines?.[venue];

  // No theme hook. The venue palettes are branding for places the fund trades;
  // wearing Polymarket's blue over a retirement notice would read as live.
  // `data-retired` desaturates the chrome too, so the whole window reads as
  // switched off rather than just this card.
  useEffect(() => {
    document.documentElement.setAttribute("data-retired", "true");
    return () => document.documentElement.removeAttribute("data-retired");
  }, []);

  return (
    <div className="p-5">
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.28 }}>
        <GlassCard className="p-8 max-w-3xl mx-auto mt-10">
          <PanelTitle right={<Pill tone="warn">retired</Pill>}>{title}</PanelTitle>

          {/* Grayscale, low contrast, no accent colour anywhere — the page
              should look switched off at a glance, before a word is read. */}
          <div className="mt-6 flex items-start gap-4">
            <div className="w-10 h-10 shrink-0 rounded-full border border-white/12
                            bg-white/[0.04] grid place-items-center text-white/30 text-lg">
              ⏻
            </div>
            <div className="space-y-4">
              <div className="text-[15px] text-white/75 leading-relaxed">
                {engine?.reason ?? "This venue has been retired and is no longer traded."}
              </div>

              <div className="text-[12.5px] text-white/45 leading-relaxed space-y-2">
                <p>
                  The adapter, its tests and its trade history are all still on
                  disk. What changed is that{" "}
                  <span className="text-white/65">the router refuses to open a
                  position here</span> — one check at the single chokepoint every
                  order passes through, so nothing downstream has to remember.
                </p>
                <p>
                  <span className="text-white/65">Exits are still permitted.</span>{" "}
                  Retiring a venue must never strand a position that is already
                  open on it; a venue you cannot trade is an inconvenience, a
                  position you cannot close is not.
                </p>
              </div>

              <div className="pt-3 border-t border-white/[0.08] text-[12.5px] text-white/45">
                The fund trades <span className="text-white/75">Robinhood</span>{" "}
                only — equities Monday to Friday, crypto at weekends, on the
                rotation in <code className="text-white/60">trading/sessions.py</code>.
                <br />
                Reversing this means removing one entry from{" "}
                <code className="text-white/60">trading/venues/retired.py</code>.
                Nothing else is switched off.
              </div>
            </div>
          </div>
        </GlassCard>
      </motion.div>
    </div>
  );
}
