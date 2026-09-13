import { AnimatePresence, motion } from "framer-motion";
import { Cpu, Pause, Play, Sparkles } from "lucide-react";
import { post, usePoll, type FundStatus, type LlmStatus } from "../lib/api";
import { useState } from "react";
import { Confirm } from "./Confirm";
import { useDocumentSurface } from "../lib/useDynamicBackground";

export type ViewKey = "overview" | "paper" | "polymarket" | "robinhood";

const VIEWS: { key: ViewKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "paper", label: "Paper Trading" },
  { key: "polymarket", label: "Polymarket" },
  { key: "robinhood", label: "Robinhood" },
];

// ---------------------------------------------------------------- toggle

function ToggleSwitch({
  on, onChange, label, tone = "amber",
}: { on: boolean; onChange: (v: boolean) => void; label: string; tone?: "amber" | "green" }) {
  const glow = tone === "amber" ? "rgba(251,191,36,0.55)" : "rgba(0,200,5,0.55)";
  // framer-motion writes these inline, where the [data-surface] CSS cannot
  // reach them — so this is the one place the ink is chosen in JS.
  const light = useDocumentSurface() === "light";
  const track = light ? "rgba(20,24,30,0.10)" : "rgba(255,255,255,0.06)";
  const knob = light ? "rgba(20,24,30,0.45)" : "rgba(255,255,255,0.45)";
  return (
    <button
      onClick={() => onChange(!on)}
      className="flex items-center gap-2.5 group"
      aria-pressed={on}
      aria-label={label}
    >
      <span className="text-xs text-white/60 whitespace-nowrap">{label}</span>
      <motion.span
        className="relative w-[42px] h-[24px] rounded-full border border-white/15 flex items-center px-[3px]"
        animate={{
          backgroundColor: on ? "rgba(251,191,36,0.22)" : track,
          boxShadow: on ? `0 0 14px ${glow}` : "0 0 0 rgba(0,0,0,0)",
        }}
        transition={{ duration: 0.22 }}
      >
        <motion.span
          layout
          className="w-[18px] h-[18px] rounded-full"
          animate={{
            x: on ? 18 : 0,
            backgroundColor: on ? "#fbbf24" : knob,
          }}
          transition={{ type: "spring", stiffness: 520, damping: 32 }}
        />
      </motion.span>
    </button>
  );
}

// ------------------------------------------------------- segmented control

function SegmentedControl({ value, onChange }: { value: ViewKey; onChange: (v: ViewKey) => void }) {
  return (
    <div className="relative flex items-center gap-1 p-1 rounded-full bg-white/[0.06] border border-white/10">
      {VIEWS.map((v) => {
        const active = v.key === value;
        return (
          <button
            key={v.key}
            onClick={() => onChange(v.key)}
            className="relative px-4 py-1.5 text-[13px] font-medium rounded-full transition-colors"
          >
            {active && (
              // layoutId is what makes the pill slide between segments rather
              // than cross-fade — the single detail that makes it read native.
              <motion.span
                layoutId="active-pill"
                className="absolute inset-0 rounded-full bg-white/[0.14] border border-white/15"
                transition={{ type: "spring", stiffness: 480, damping: 38 }}
              />
            )}
            <span className={`relative z-10 ${active ? "text-white" : "text-white/50 hover:text-white/80"}`}>
              {v.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- go / stop

function ExecButton({
  kind, disabled, onClick,
}: { kind: "go" | "stop"; disabled?: boolean; onClick: () => void }) {
  const go = kind === "go";
  return (
    <motion.button
      onClick={onClick}
      disabled={disabled}
      whileHover={disabled ? undefined : { scale: 1.05 }}
      whileTap={disabled ? undefined : { scale: 0.95 }}
      transition={{ type: "spring", stiffness: 500, damping: 25 }}
      className={[
        "flex items-center gap-1.5 px-5 py-2 rounded-full text-[13px] font-semibold",
        "border disabled:opacity-35 disabled:cursor-not-allowed",
        go
          ? "bg-gradient-to-r from-green-400 to-green-600 border-green-300/40 text-black"
          : "bg-gradient-to-r from-red-500 to-red-700 border-red-400/40 text-white",
      ].join(" ")}
      style={disabled ? undefined : {
        boxShadow: go ? "0 0 20px rgba(0,200,5,0.35)" : "0 0 20px rgba(239,68,68,0.30)",
      }}
    >
      {go ? <Play size={13} fill="currentColor" /> : <Pause size={13} fill="currentColor" />}
      {go ? "Go" : "Stop"}
    </motion.button>
  );
}

// ---------------------------------------------------------------- bar

export function StatusBar({ view, onView }: { view: ViewKey; onView: (v: ViewKey) => void }) {
  const { data: fund, refresh } = usePoll<FundStatus>("/api/fund", 4000);
  const { data: llm } = usePoll<LlmStatus>("/api/llm", 6000);

  const running = fund?.state === "running" || fund?.state === "starting";
  // The master switch. Both directions are confirmed: GO commits the machine to
  // trading, STOP takes the whole service down mid-cycle.
  const [ask, setAsk] = useState<null | "start" | "stop">(null);
  const equitiesOpen = !!fund?.equities_open;

  // The spec's mockup hardcodes "GPT-4o & Claude 3.5 Sonnet". The live stack is
  // Claude Opus 5 with Gemini failover, and which model formed an opinion
  // changes how much that opinion is worth — so this reads the real router.
  const onGemini = llm?.active_provider === "gemini";
  const used = llm?.limits?.percent_used;
  const modelLabel = llm?.attached
    ? `${onGemini ? "Gemini" : "Claude"} · ${llm.active_model ?? "—"}`
    : "no model attached";

  return (
    <header className="sticky top-0 z-50 px-4 pt-3">
      <div
        className="glass-plain bg-glass-white border border-glass-border rounded-3xl px-6 py-3.5
                   grid grid-cols-[1fr_auto_1fr] items-center gap-4"
        style={{ boxShadow: "0 8px 32px 0 rgba(31,38,135,0.37), inset 0 1px 0 rgba(255,255,255,0.15)" }}
      >
        {/* left */}
        <div className="flex items-center gap-5">
          <ToggleSwitch
            label="Paper mode"
            on={!fund?.router_live_gate?.live_possible}
            onChange={() => { /* live flip is the rule-#13 checklist, not a switch */ }}
          />
          <SegmentedControl value={view} onChange={onView} />
        </div>

        {/* centre */}
        <div className="flex flex-col items-center min-w-[220px]">
          <h1 className="text-2xl font-semibold tracking-tighter text-white leading-none">
            Project <span className="font-normal">धन</span>
          </h1>
          <div
            className="flex items-center gap-1.5 mt-1 text-[10px] uppercase tracking-[0.14em]"
            title={llm?.reason ?? ""}
          >
            {onGemini ? <Sparkles size={11} className="text-amber-400" /> : <Cpu size={11} className="text-white/40" />}
            <span className={onGemini ? "text-amber-400" : "text-white/40"}>{modelLabel}</span>
            {used != null && !onGemini && (
              <span className={used >= (llm?.failover_threshold_pct ?? 85) ? "text-amber-400" : "text-white/30"}>
                · {used.toFixed(0)}%
              </span>
            )}
          </div>
        </div>

        {/* right */}
        <div className="flex items-center justify-end gap-5">
          <div className="flex flex-col gap-0.5 text-right">
            <div className="flex items-center justify-end gap-2">
              <span className="text-[11px] text-white/45">Weekdays (Equity)</span>
              <span className={`text-[11px] font-bold ${equitiesOpen ? "text-hood-green" : "text-white/30"}`}>
                {equitiesOpen ? "Active" : "Closed"}
              </span>
            </div>
            <div className="flex items-center justify-end gap-2">
              <span className="text-[11px] text-white/45">Weekends (Crypto)</span>
              <span className={`text-[11px] font-bold ${equitiesOpen ? "text-white/30" : "text-poly-blue"}`}>
                {equitiesOpen ? "Standby" : "Active"}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2 pl-4 border-l border-white/10">
            <AnimatePresence mode="wait">
              <motion.span
                key={fund?.state ?? "none"}
                initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 4 }}
                className={`text-[11px] px-2.5 py-1 rounded-full border ${
                  running ? "text-hood-green border-hood-green/40 bg-hood-green/10"
                          : "text-white/40 border-white/15"}`}
              >
                {fund?.state ?? "—"}
              </motion.span>
            </AnimatePresence>
            <ExecButton kind="go" disabled={running || !fund?.attached}
              onClick={() => setAsk("start")} />
            <ExecButton kind="stop" disabled={!running}
              onClick={() => setAsk("stop")} />
          </div>
        </div>
      </div>

      <Confirm
        open={ask === "start"}
        title="Start Project धन?"
        confirmLabel="Start"
        body={<>
          The fund begins cycling in <b className="text-white/80">paper mode only</b>.
          Live trading needs two more things you have to switch on yourself: the
          venue engine, and Go&nbsp;Live in that venue&rsquo;s live section — which
          the rule-#13 checklist still has to allow.
        </>}
        onCancel={() => setAsk(null)}
        onConfirm={async () => { setAsk(null); await post("/api/start"); void refresh(); }} />

      <Confirm
        open={ask === "stop"}
        title="Stop Project धन?"
        confirmLabel="Stop everything"
        tone="bad"
        body={<>
          Stops the whole service. In-flight cycles are cancelled; orders already
          resting at a venue are <b className="text-white/80">not</b> cancelled,
          and open positions stay open.
        </>}
        onCancel={() => setAsk(null)}
        onConfirm={async () => { setAsk(null); await post("/api/stop"); void refresh(); }} />
    </header>
  );
}

