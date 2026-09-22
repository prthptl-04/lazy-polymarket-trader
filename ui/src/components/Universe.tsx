import { GlassCard, PanelTitle } from "./GlassCard";
import { Empty, Pill } from "./primitives";
import { usePoll, NEAR, type FundStatus } from "../lib/api";

/**
 * What the fund looked at this cycle, and what it threw away.
 *
 * This became worth a panel when the crypto universe stopped being a hand-typed
 * pair: the scout screens 58 tradable Robinhood pairs down to a handful, and
 * the cycle report used to say only "12". Twelve of what, and why not the other
 * forty-six?
 *
 * The rejections are the more useful half. A candidate dropped for "no exit
 * plan" is a data problem; one dropped for a distress-zone balance sheet is the
 * screen working. Reporting only a count makes those identical.
 */
export function Universe() {
  const { data } = usePoll<FundStatus>("/api/fund", NEAR);
  const cycle = data?.last_cycle;

  if (!cycle) {
    return (
      <GlassCard className="p-4" inert>
        <PanelTitle>Universe · what the screen found</PanelTitle>
        <Empty title="No cycle has completed yet."
               hint="The screen runs at the top of each cycle, before any model is consulted." />
      </GlassCard>
    );
  }

  const names = cycle.universe_names ?? [];
  const debated = new Set(cycle.deliberated_names ?? []);
  const rejects = cycle.prescreen_rejections ?? [];

  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={
        <div className="flex items-center gap-1.5">
          <Pill>{cycle.session}</Pill>
          <Pill tone={names.length ? "neutral" : "warn"}>{names.length} screened</Pill>
        </div>
      }>Universe · what the screen found</PanelTitle>

      {names.length === 0 ? (
        <Empty title="Nothing survived the screen this cycle."
               hint="Deterministic filters run before any model is consulted, so this costs nothing but says the market was quiet." />
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5">
            {names.map((sym) => (
              <span key={sym}
                className={`text-[11px] font-mono rounded-lg px-2 py-1 border ${
                  debated.has(sym)
                    ? "border-hood-green/35 bg-hood-green/[0.08] text-hood-green"
                    : "border-white/[0.09] bg-white/[0.03] text-white/55"}`}
                title={debated.has(sym) ? "debated this cycle" : "screened, not debated"}>
                {sym}
              </span>
            ))}
          </div>
          <div className="text-[10px] text-white/35 mt-2">
            Green reached the round table. The rest passed the screen but were
            beyond this cycle's candidate budget.
          </div>
        </>
      )}

      {rejects.length > 0 && (
        <div className="mt-3 pt-3 border-t border-white/[0.08]">
          <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1.5">
            Rejected before any model was consulted
          </div>
          <div className="space-y-1">
            {rejects.slice(0, 8).map((r, i) => (
              <div key={i} className="flex gap-2 items-baseline text-[11px]">
                <span className="font-mono text-white/50 w-[92px] shrink-0">{r.symbol}</span>
                <span className="text-white/40 leading-snug">{r.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </GlassCard>
  );
}
