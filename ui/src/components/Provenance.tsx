import { GlassCard, PanelTitle } from "./GlassCard";
import { Pill } from "./primitives";
import { usePoll, SLOW, type Provenance as Prov } from "../lib/api";

/**
 * How old was what the committee read?
 *
 * A transcript without its evidence records the verdict and destroys the
 * inputs. This is the other half — the sources behind the most recent
 * deliberation, each with its age, and a warning on anything the fund could
 * not refresh.
 *
 * Stale is shown, never hidden. Evidence the fund could not refresh is still
 * the best it has; dropping it would leave a reader believing the committee
 * reasoned from nothing when it reasoned from something old.
 */
export function Provenance({ thesisId }: { thesisId?: string }) {
  const { data } = usePoll<Prov>(
    thesisId ? `/api/deliberations/${thesisId}` : "/api/roundtable/latest", SLOW);

  const sources = data?.sources ?? [];
  if (!data || sources.length === 0) return null;

  const now = Date.now() / 1000;
  const stale = sources.filter((s) => !s.derived && (s.as_of == null || now - s.as_of > 3600));

  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={
        stale.length
          ? <Pill tone="warn">{stale.length} stale</Pill>
          : <Pill tone="good">all fresh</Pill>
      }>Provenance · what the seats read, and when it was true</PanelTitle>

      <div className="space-y-1.5">
        {sources.map((s, i) => {
          const age = s.as_of == null ? null : now - s.as_of;
          const isStale = !s.derived && (age == null || age > 3600);
          return (
            <div key={i} className="flex items-baseline justify-between gap-3">
              <div className="flex items-baseline gap-2 min-w-0">
                <span className="text-[11px] text-white/70 w-[84px] shrink-0">{s.kind}</span>
                <span className="text-[11px] text-white/40 truncate">{s.source}</span>
              </div>
              <span className={`font-mono text-[10px] shrink-0 ${
                s.derived ? "text-white/30"
                  : isStale ? "text-amber-400/90" : "text-white/45"}`}>
                {s.derived ? "computed" : age == null ? "age unknown" : humanAge(age)}
                {isStale && !s.derived && " · STALE"}
              </span>
            </div>
          );
        })}
      </div>

      {stale.length > 0 && (
        <div className="text-[10px] text-amber-400/70 mt-2.5 pt-2.5 border-t border-white/[0.08] leading-relaxed">
          The seats were told which sources were stale and instructed to lower
          confidence rather than assume they still hold.
        </div>
      )}
    </GlassCard>
  );
}

/** Mirrors `roundtable.knowledge._human` so the two never disagree on screen. */
function humanAge(seconds: number): string {
  if (seconds < 90) return `${seconds.toFixed(0)}s ago`;
  if (seconds < 5400) return `${(seconds / 60).toFixed(0)}m ago`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(0)}h ago`;
  return `${(seconds / 86400).toFixed(0)}d ago`;
}
