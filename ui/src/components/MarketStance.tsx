import { useState } from "react";
import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill } from "./primitives";
import { usePoll, type LatestDebate, NEAR
} from "../lib/api";

/**
 * YES / NO — the committee's side, not an order ticket.
 *
 * Rule #18 makes the dashboard read-only apart from GO/STOP: there are no
 * manual trade buttons, because a click that can open a position is a path
 * around the grader and the live gate. So these select which side's *argument*
 * you are reading. The default selection is the side the table actually took.
 */
export function MarketStance() {
  const { data: debate } = usePoll<LatestDebate>("/api/roundtable/latest", NEAR);
  const [side, setSide] = useState<"yes" | "no" | null>(null);

  const consensus = debate?.consensus?.signal;
  const taken: "yes" | "no" | null =
    consensus === "bullish" ? "yes" : consensus === "bearish" ? "no" : null;
  const shown = side ?? taken;

  const wanted = shown === "yes" ? "bullish" : "bearish";
  const voices = (debate?.opinions ?? []).filter((o) => !o.failed && o.signal === wanted);

  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={debate ? <Pill>{debate.symbol}</Pill> : undefined}>
        Market side · committee
      </PanelTitle>

      <div className="grid grid-cols-2 gap-2.5">
        {(["yes", "no"] as const).map((s) => (
          <button key={s} type="button"
            onClick={() => setSide(s)}
            data-selected={shown === s}
            aria-pressed={shown === s}
            className="pm-action py-2.5 text-[13px] font-semibold tracking-wide uppercase">
            {s}
            {taken === s && <span className="block text-[9px] font-normal opacity-70 normal-case">
              table&rsquo;s call
            </span>}
          </button>
        ))}
      </div>

      {debate ? (
        <div className="mt-3 space-y-2">
          {voices.length ? voices.map((o) => (
            <div key={o.seat_id} className="text-[11px] leading-relaxed">
              <span className="font-semibold text-white/85">{o.seat_name}</span>
              <span className="text-white/40 font-mono"> {o.confidence.toFixed(0)}</span>
              <span className="text-white/55"> · {o.reasoning}</span>
            </div>
          )) : (
            <div className="text-[11px] text-white/40">
              No seat argued {shown === "yes" ? "YES" : "NO"} on {debate.symbol}.
            </div>
          )}
          <div className="text-[10px] text-white/30 pt-2 border-t border-white/[0.08]">
            Reading only. Orders come from the round table through the grader and
            the live gate — never from this panel.
          </div>
        </div>
      ) : (
        <Empty title="No market under discussion."
               hint="The committee convenes once a cycle finds a candidate that survives the deterministic screens." />
      )}
    </GlassCard>
  );
}
