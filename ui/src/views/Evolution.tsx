import { motion } from "framer-motion";
import { GlassCard, PanelTitle } from "../components/GlassCard";
import { Empty, Pill } from "../components/primitives";
import { usePoll, LIVE, type Evolution as Evo, type EvoSeat } from "../lib/api";

/**
 * How the fund changes its own mind.
 *
 * The framing is borrowed from agents 2.0 (aiwaves-cn/agents): an agent
 * pipeline is a computational graph — a node is a layer, its prompts and tools
 * are that layer's weights, and textual reflections back-propagate as
 * "language gradients". Drawing it that way is only honest because this fund
 * already works that way, and every number on this page is read from the
 * component that acts on it rather than composed for the picture:
 *
 *   forward   what is drawn left to right, one node per real stage
 *   weights   `vote_weight` is what the chair's tally multiplies by
 *   loss      realised outcomes, scored — blank when nothing has resolved
 *   backward  the dashed edges, and the lesson text is the literal string
 *             `recent_lesson_lines` puts into the next debate's evidence
 *
 * The one thing this page must never do is animate a loop that is not turning.
 * The current only flows while a debate is actually in progress, the backward
 * edges are dimmed while the lesson gate is holding, and an unscored seat is
 * drawn at full weight because that is how it votes.
 */

const W = 1000, H = 430;
const COL = { evidence: 74, seats: 300, chair: 508, grader: 646, router: 784, outcome: 922 };

const seatY = (i: number, n: number) => 46 + (i * (300 / Math.max(1, n - 1)));
const inputY = (i: number, n: number) => 90 + (i * (240 / Math.max(1, n - 1)));
const SPINE = 196;

/** A cubic that leaves horizontally and arrives horizontally — reads as a wire. */
const wire = (x1: number, y1: number, x2: number, y2: number) => {
  const dx = (x2 - x1) * 0.5;
  return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
};

const tone = (s: string | null | undefined) =>
  s === "bullish" ? "#00c805" : s === "bearish" ? "#ff5000" : "#8b95a5";

export function Evolution() {
  const { data } = usePoll<Evo>("/api/evolution", LIVE);

  if (!data) {
    return (
      <GlassCard className="p-5" inert>
        <PanelTitle>Self-evolution</PanelTitle>
        <Empty title="The loop has not reported yet."
               hint="This page reads the live committee, the calibration fit and the post-mortem gate. It renders nothing until all three answer." />
      </GlassCard>
    );
  }

  const evidence = data.forward[0];
  const seatsStage = data.forward[1];
  const inputs = evidence.inputs ?? [];
  const seats = data.weights.seats;
  const live = !!seatsStage.active;
  const shrink = data.weights.confidence_shrink;

  return (
    <div className="space-y-4 evo-root">
      <style>{`
        /* The [data-surface="light"] rules in index.css rewrite Tailwind ink
           classes; they cannot touch an inline SVG fill. One variable, resolved
           from the same --dhan-ink-rgb, keeps the graph legible on both. */
        .evo-root { --evo-ink: 255 255 255; }
        [data-surface="light"] .evo-root { --evo-ink: var(--dhan-ink-rgb, 20 24 30); }
        @keyframes evo-flow { to { stroke-dashoffset: -28; } }
        .evo-live { stroke-dasharray: 5 9; animation: evo-flow 1.1s linear infinite; }
        .evo-back { stroke-dasharray: 3 7; }
        .evo-back-live { stroke-dasharray: 3 7; animation: evo-flow 2.4s linear infinite reverse; }
      `}</style>

      {/* ------------------------------------------------ the graph */}
      <GlassCard className="p-4" inert>
        <PanelTitle right={
          <div className="flex items-center gap-1.5">
            {live
              ? <Pill tone="good">deliberating · {seatsStage.symbol}</Pill>
              : <Pill>idle</Pill>}
            <span className="font-mono text-[10px] text-white/35">
              {seatsStage.answered}/{seatsStage.expected} seats
            </span>
          </div>
        }>
          Self-evolution · the pipeline as a computational graph
        </PanelTitle>

        <p className="text-[11.5px] text-white/45 leading-relaxed mb-1 max-w-3xl">
          A node is a layer, its prompts are that layer's weights, and a post-mortem
          is a gradient. Solid wires carry a decision forward; dashed ones carry what
          the outcome taught back into the seats that will vote next.
        </p>

        <div className="overflow-x-auto">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[720px]" style={{ height: "auto" }}>
            <defs>
              <radialGradient id="evo-node">
                <stop offset="0%" stopColor="rgb(var(--evo-ink) / 0.14)" />
                <stop offset="100%" stopColor="rgb(var(--evo-ink) / 0.03)" />
              </radialGradient>
            </defs>

            {/* forward: evidence -> each seat */}
            {inputs.map((inp, i) =>
              seats.map((s, j) => (
                <path key={`${inp.id}-${s.seat_id}`}
                  d={wire(COL.evidence + 46, inputY(i, inputs.length), COL.seats - 24, seatY(j, seats.length))}
                  fill="none" strokeWidth={0.6}
                  stroke={inp.attached ? "rgba(120,190,255,0.20)" : "rgb(var(--evo-ink) / 0.05)"}
                  className={live && inp.attached ? "evo-live" : undefined} />
              ))
            )}

            {/* forward: each seat -> chair, thickness = the weight it votes with */}
            {seats.map((s, j) => (
              <path key={`v-${s.seat_id}`}
                d={wire(COL.seats + 24, seatY(j, seats.length), COL.chair - 26, SPINE)}
                fill="none" strokeLinecap="round"
                strokeWidth={0.8 + s.vote_weight * 2.2}
                stroke={s.state === "answered" ? tone(s.signal) : "rgb(var(--evo-ink) / 0.16)"}
                strokeOpacity={s.state === "answered" ? 0.55 : 1}
                className={s.state === "thinking" ? "evo-live" : undefined} />
            ))}

            {/* the spine: chair -> grader -> router -> outcome */}
            {[[COL.chair, COL.grader], [COL.grader, COL.router], [COL.router, COL.outcome]].map(([a, b]) => (
              <path key={a} d={wire(a + 26, SPINE, b - 26, SPINE)} fill="none"
                    strokeWidth={2} stroke="rgb(var(--evo-ink) / 0.22)"
                    className={live ? "evo-live" : undefined} />
            ))}

            {/* backward: outcome -> seat weights, and outcome -> sizing */}
            <path d={`M${COL.outcome},${SPINE + 30} C${COL.outcome},${H - 22} ${COL.seats},${H - 22} ${COL.seats},${seatY(seats.length - 1, seats.length) + 24}`}
                  fill="none" strokeWidth={1.6} stroke="rgba(251,191,36,0.55)"
                  className={data.backward.injecting ? "evo-back-live" : "evo-back"}
                  strokeOpacity={data.backward.injecting ? 1 : 0.35} />
            <path d={`M${COL.outcome},${SPINE + 30} C${COL.outcome},${H - 60} ${COL.chair + 60},${H - 60} ${COL.chair},${SPINE + 28}`}
                  fill="none" strokeWidth={1.6} stroke="rgba(167,139,250,0.55)"
                  className={shrink.usable ? "evo-back-live" : "evo-back"}
                  strokeOpacity={shrink.usable ? 1 : 0.35} />
            <text x={(COL.seats + COL.outcome) / 2} y={H - 8} textAnchor="middle"
                  className="fill-current" fontSize={9.5}
                  style={{ fill: "rgba(251,191,36,0.75)" }}>
              language gradient · {data.backward.injecting ? "flowing into the next debate" : "held below the sample bar"}
            </text>

            {/* ---- evidence nodes ---- */}
            {inputs.map((inp, i) => (
              <g key={inp.id} opacity={inp.attached ? 1 : 0.4}>
                <rect x={COL.evidence - 46} y={inputY(i, inputs.length) - 15} width={92} height={30} rx={9}
                      fill="url(#evo-node)" stroke={inp.attached ? "rgba(120,190,255,0.45)" : "rgb(var(--evo-ink) / 0.14)"} />
                <text x={COL.evidence} y={inputY(i, inputs.length) + 3.5} textAnchor="middle"
                      fontSize={10} style={{ fill: "rgb(var(--evo-ink) / 0.8)" }}>{inp.label}</text>
              </g>
            ))}
            <text x={COL.evidence} y={22} textAnchor="middle" fontSize={9.5}
                  style={{ fill: "rgb(var(--evo-ink) / 0.3)" }}>EVIDENCE</text>

            {/* ---- seat nodes: radius carries the vote weight ---- */}
            {seats.map((s, j) => {
              const r = 11 + s.vote_weight * 7;
              const y = seatY(j, seats.length);
              return (
                <g key={s.seat_id}>
                  {s.state === "thinking" && (
                    <motion.circle cx={COL.seats} cy={y} r={r}
                      fill="none" stroke="rgba(120,190,255,0.5)" strokeWidth={1}
                      initial={{ scale: 1, opacity: 0.6 }}
                      animate={{ scale: 1.9, opacity: 0 }}
                      transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }}
                      style={{ transformOrigin: `${COL.seats}px ${y}px` }} />
                  )}
                  <circle cx={COL.seats} cy={y} r={r} fill="url(#evo-node)"
                          stroke={s.state === "answered" ? tone(s.signal) : "rgb(var(--evo-ink) / 0.28)"}
                          strokeWidth={s.scored ? 1.8 : 1} />
                  <text x={COL.seats} y={y + 3} textAnchor="middle" fontSize={9}
                        fontFamily="ui-monospace, monospace"
                        style={{ fill: "rgb(var(--evo-ink) / 0.85)" }}>
                    {s.vote_weight.toFixed(2)}
                  </text>
                  <text x={COL.seats - r - 8} y={y + 3} textAnchor="end" fontSize={9.5}
                        style={{ fill: "rgb(var(--evo-ink) / 0.55)" }}>{s.seat_name}</text>
                </g>
              );
            })}
            <text x={COL.seats} y={22} textAnchor="middle" fontSize={9.5}
                  style={{ fill: "rgb(var(--evo-ink) / 0.3)" }}>SEATS · vote weight</text>

            {/* ---- the spine nodes ---- */}
            {(["chair", "grader", "router", "outcome"] as const).map((id) => {
              const stage = data.forward.find((s) => s.id === id)!;
              const x = COL[id];
              return (
                <g key={id}>
                  <circle cx={x} cy={SPINE} r={26} fill="url(#evo-node)"
                          stroke="rgb(var(--evo-ink) / 0.3)" strokeWidth={1.4} />
                  <text x={x} y={SPINE + 3} textAnchor="middle" fontSize={9}
                        style={{ fill: "rgb(var(--evo-ink) / 0.85)" }}>
                    {stage.label.split(" ")[0]}
                  </text>
                  {id === "outcome" && (
                    <text x={x} y={SPINE + 44} textAnchor="middle" fontSize={9}
                          fontFamily="ui-monospace, monospace"
                          style={{ fill: "rgb(var(--evo-ink) / 0.45)" }}>
                      {stage.closed} closed
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        </div>
      </GlassCard>

      {/* ------------------------------------------------ the three passes */}
      <div className="grid gap-4 lg:grid-cols-3">
        <WeightsPanel seats={seats} shrink={shrink} />
        <LossPanel loss={data.loss} />
        <BackwardPanel back={data.backward} />
      </div>

      <StagesPanel stages={data.forward} />
    </div>
  );
}

// ---------------------------------------------------------------- panels

function WeightsPanel({ seats, shrink }: {
  seats: EvoSeat[]; shrink: Evo["weights"]["confidence_shrink"];
}) {
  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={<Pill>weights</Pill>}>How loudly each seat counts</PanelTitle>
      <div className="space-y-2">
        {seats.map((s) => (
          <div key={s.seat_id}>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[11px] text-white/70">{s.seat_name}</span>
              <span className="font-mono text-[10.5px] text-white/85">{s.vote_weight.toFixed(2)}×</span>
            </div>
            <div className="h-1.5 rounded-full bg-white/[0.06] mt-1 overflow-hidden">
              <motion.div className="h-full rounded-full"
                style={{ background: s.scored ? "rgba(0,200,5,0.6)" : "rgb(var(--evo-ink) / 0.22)" }}
                initial={false}
                animate={{ width: `${(s.vote_weight / 1.5) * 100}%` }}
                transition={{ type: "spring", stiffness: 120, damping: 20 }} />
            </div>
            <div className="text-[10px] text-white/35 mt-0.5">
              {s.scored
                ? `Brier ${s.brier?.toFixed(3)} · ${s.overconfidence! > 0 ? "+" : ""}${s.overconfidence?.toFixed(0)} pts confidence`
                : `${s.samples}/${s.min_samples} calls — unscored seats vote at full weight`}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-3 pt-3 border-t border-white/[0.08]">
        <div className="flex items-baseline justify-between">
          <span className="text-[11px] text-white/70">Confidence shrink</span>
          <span className="font-mono text-[10.5px] text-white/85">
            {shrink.usable ? shrink.shrink!.toFixed(3) : "—"}
          </span>
        </div>
        <div className="text-[10px] text-white/35 mt-0.5 leading-relaxed">{shrink.reason}</div>
      </div>
    </GlassCard>
  );
}

function LossPanel({ loss }: { loss: Evo["loss"] }) {
  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={<Pill tone={loss.closed_trades ? "neutral" : "warn"}>loss</Pill>}>
        What the outcomes scored
      </PanelTitle>
      {!loss.closed_trades ? (
        <Empty title="Nothing has resolved." hint={loss.reason} />
      ) : (
        <div className="space-y-2.5">
          <Row label="Resolved theses" value={String(loss.closed_trades)} />
          {loss.committee && <>
            <Row label="Committee hit rate" value={`${(loss.committee.hit_rate * 100).toFixed(1)}%`} />
            <Row label="Committee Brier" value={loss.committee.brier.toFixed(3)}
                 sub="0.25 is a coin flip that admits it" />
            <Row label="Overconfidence" value={`${loss.committee.overconfidence > 0 ? "+" : ""}${loss.committee.overconfidence.toFixed(1)} pts`}
                 sub="stated confidence minus what actually happened" />
          </>}
          {loss.worst_seat && (
            <div className="pt-2 mt-1 border-t border-white/[0.08]">
              <div className="text-[10px] text-white/35">Furthest from calibrated</div>
              <div className="text-[11.5px] text-white/75 mt-0.5">
                {loss.worst_seat.seat_name}
                <span className="font-mono text-[10.5px] text-amber-400/80 ml-1.5">
                  {loss.worst_seat.overconfidence > 0 ? "+" : ""}{loss.worst_seat.overconfidence.toFixed(1)} pts
                </span>
              </div>
            </div>
          )}
          <div className="text-[10px] text-white/35 leading-relaxed pt-1">{loss.reason}</div>
        </div>
      )}
    </GlassCard>
  );
}

function BackwardPanel({ back }: { back: Evo["backward"] }) {
  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={
        <Pill tone={back.injecting ? "good" : "warn"}>
          {back.injecting ? "flowing" : "held"}
        </Pill>
      }>The gradient going back</PanelTitle>
      {/* Retrieval is by relevance, so the set below is filtered. Saying which
          filter stops it reading as "everything the fund has learned". */}
      <div className="text-[10px] text-white/35 mb-2">
        {back.scope
          ? <>Retrieved for <span className="text-white/60">{back.scope.symbol}</span>
              {back.scope.asset_class && <> · {back.scope.asset_class}</>} — lessons from
              another market are not evidence here.</>
          : <>No debate in progress — showing the whole corpus, unscoped.</>}
      </div>
      <div className="space-y-2">
        {back.lessons.map((l, i) => (
          <motion.div key={i}
            initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.04 }}
            className="rounded-xl border border-amber-400/15 bg-amber-400/[0.05] px-2.5 py-2">
            <div className="text-[11px] text-white/70 leading-relaxed">{l.text}</div>
          </motion.div>
        ))}
      </div>
      <div className="text-[10px] text-white/35 mt-3 leading-relaxed">
        {back.reason}
        <div className="mt-1 font-mono text-white/45">
          {back.recorded} recorded · {back.min_samples} resolved trades needed to inject
        </div>
      </div>
    </GlassCard>
  );
}

function StagesPanel({ stages }: { stages: Evo["forward"] }) {
  return (
    <GlassCard className="p-4" inert>
      <PanelTitle right={<Pill>forward pass</Pill>}>What each layer is for</PanelTitle>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {stages.map((s, i) => (
          <div key={s.id} className="rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2">
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-[10px] text-white/30">{i + 1}</span>
              <span className="text-[11.5px] font-semibold text-white/80">{s.label}</span>
            </div>
            <div className="text-[11px] text-white/50 mt-1 leading-relaxed">{s.detail}</div>
            {!!s.inputs?.length && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {s.inputs.map((inp) => (
                  <span key={inp.id}
                    className={`text-[9.5px] rounded-full px-1.5 py-0.5 border ${
                      inp.attached
                        ? "border-sky-400/25 text-sky-300/80"
                        : "border-white/10 text-white/25"}`}
                    title={inp.detail}>
                    {inp.label}{inp.source ? ` · ${inp.source}` : ""}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </GlassCard>
  );
}

function Row({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] text-white/70">{label}</span>
        <span className="font-mono text-[11px] text-white/85">{value}</span>
      </div>
      {sub && <div className="text-[10px] text-white/30 mt-0.5">{sub}</div>}
    </div>
  );
}
