import { useState } from "react";
import { motion } from "framer-motion";
import { getJSON, post, type EngineState } from "../lib/api";

/**
 * Start a venue's connection.
 *
 * Starting is what authenticates and opens the data path, so it is the state
 * worth guarding: disabled while the system is stopped, while no adapter is
 * registered, or while the venue is unauthenticated — each with the reason
 * printed underneath, because a greyed-out button that will not say why is
 * indistinguishable from a broken one.
 *
 * Stopping is never disabled. You must always be able to cut a venue off.
 */
export function EngineButton({ venue, label, state, onDone }: {
  venue: string; label: string; state?: EngineState; onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const on = !!state?.on;
  const blocked = !state?.attached ? state?.reason ?? "no adapter registered"
    : !state?.system_running ? "Project धन is stopped — start the system first"
    : !state?.authenticated ? state?.reason ?? "not authenticated"
    : null;
  const disabled = busy || (!on && !!blocked);

  const click = async () => {
    setBusy(true); setError(null);
    const ok = await post(`/api/engines/${venue}/${on ? "stop" : "start"}`);
    if (!ok) {
      const fresh = await getJSON<{ engines: Record<string, EngineState> }>("/api/engines");
      setError(fresh?.engines?.[venue]?.reason ?? "the engine refused to start");
    }
    setBusy(false);
    onDone();
  };

  return (
    <div className="mb-2">
      <motion.button
        onClick={click}
        disabled={disabled}
        whileHover={disabled ? undefined : { scale: 1.015 }}
        whileTap={disabled ? undefined : { scale: 0.985 }}
        className={`engine-button w-full py-2 rounded-xl text-[12px] font-semibold tracking-wide border transition
          ${on ? "border-hood-green/45 bg-hood-green/15 text-hood-green"
               : disabled ? "border-white/10 bg-white/[0.03] text-white/25 cursor-not-allowed"
                          : "border-green-400/40 bg-gradient-to-r from-green-400 to-green-600 text-black"}`}>
        <span className="inline-flex items-center gap-2">
          <span className={`w-1.5 h-1.5 rounded-full ${on ? "bg-hood-green" : "bg-current opacity-60"}`} />
          {busy ? "…" : on ? `${label} · connected — stop` : `Start the ${label}`}
        </span>
      </motion.button>
      {(error || (!on && blocked)) && (
        <div className="text-[10.5px] text-white/35 mt-1 leading-snug">{error ?? blocked}</div>
      )}
      {on && <div className="text-[10.5px] text-white/35 mt-1">
        Authenticated and fetching data. Trading mode is chosen on the venue page.
      </div>}
    </div>
  );
}
