import { GlassCard, PanelTitle } from "./GlassCard";
import { Pill, money, signed, toneOf } from "./primitives";
import { usePoll, type Costs, type ModeSpend } from "../lib/api";

const tokens = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(2)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}k` : `${n}`;

const usd = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `$${v.toFixed(digits)}`;

/**
 * What the committee costs, against what it earned — per mode.
 *
 * The seats read this same ledger in their evidence block before every debate,
 * so the panel and the agents are looking at one set of numbers rather than the
 * dashboard's version of them.
 *
 * A mode that has never executed shows earnings as unknown rather than $0.00.
 * Zero reads as break-even; it has not broken even, it has not been tried.
 */
export function CostMatrix() {
  const { data } = usePoll<Costs>("/api/costs", 15000);
  if (!data) return null;

  return (
    <GlassCard className="col-span-full p-5" inert>
      <PanelTitle right={<Pill tone={data.total_burned_usd > 0 ? "warn" : "neutral"}>
        {usd(data.total_burned_usd, 4)} total model spend
      </Pill>}>
        AI session cost · burn versus earn
      </PanelTitle>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-0">
        <ModeColumn title="Paper" tone="text-amber-400/80" spend={data.modes.paper}
                    className="lg:pr-6" />
        <ModeColumn title="Live" tone="text-hood-green" spend={data.modes.live}
                    className="lg:pl-6 lg:border-l border-white/[0.09] mt-5 lg:mt-0" />
      </div>

      <div className="text-[10.5px] text-white/35 mt-4 pt-3 border-t border-white/[0.07] leading-relaxed">
        Rates: ${data.pricing.input_per_mtok}/M in, ${data.pricing.output_per_mtok}/M out —{" "}
        {data.pricing.source}. Cached reads bill at{" "}
        {data.pricing.cache_read_multiplier}× input, cache writes at{" "}
        {data.pricing.cache_write_multiplier}×.
        {data.note && <> {data.note}</>}
      </div>
    </GlassCard>
  );
}

function ModeColumn({ title, tone, spend, className = "" }: {
  title: string; tone: string; spend: ModeSpend; className?: string;
}) {
  const cached = spend.cache_read + spend.cache_write;
  const total = spend.input_tokens + spend.output_tokens + cached;
  const cacheShare = total ? Math.round((spend.cache_read / total) * 100) : 0;

  return (
    <div className={className}>
      <div className={`text-[10px] uppercase tracking-[0.16em] font-semibold mb-3 ${tone}`}>
        {title}
      </div>
      {spend.calls ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-5 gap-y-4">
          <Cell label="Model calls" value={`${spend.calls}`} />
          <Cell label="Burned" value={usd(spend.burned_usd, 4)} tone="text-red-400" />
          <Cell label="Earned"
                value={spend.earned_usd == null ? "unknown" : signed(spend.earned_usd)}
                tone={spend.earned_usd == null ? "text-white/35" : toneOf(spend.earned_usd)}
                sub={spend.earned_usd == null ? "never executed" : "realised"} />
          <Cell label="Net"
                value={spend.net_usd == null ? "—" : signed(spend.net_usd)}
                tone={spend.net_usd == null ? undefined : toneOf(spend.net_usd)} />
          <Cell label="Per $1 spent"
                value={spend.earn_per_dollar == null ? "—" : `${money(spend.earn_per_dollar)}`}
                sub="earned per dollar of model" />
          <Cell label="Cost per call" value={usd(spend.cost_per_call_usd, 4)} />
          <Cell label="Tokens in / out"
                value={`${tokens(spend.input_tokens)} / ${tokens(spend.output_tokens)}`} />
          <Cell label="Cache read / write"
                value={`${tokens(spend.cache_read)} / ${tokens(spend.cache_write)}`}
                sub={`${cacheShare}% of tokens served from cache`} />
        </div>
      ) : (
        <div className="text-[11.5px] text-white/35 py-6">
          No model calls in {title.toLowerCase()} mode yet.
        </div>
      )}
    </div>
  );
}

function Cell({ label, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-[0.09em] text-white/35">{label}</div>
      <div className={`font-mono text-[15px] font-semibold mt-0.5 ${tone ?? "text-white/85"}`}>
        {value}
      </div>
      {sub && <div className="text-[9.5px] text-white/30 mt-0.5">{sub}</div>}
    </div>
  );
}
