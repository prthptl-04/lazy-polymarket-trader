import { motion } from "framer-motion";
import { usePoll, type Agent } from "../lib/api";

/**
 * Seven agents in a ring, drawn from polar coordinates.
 *
 *   θ = 2π / n           angular step
 *   x = R·cos(θi − π/2)  the −π/2 anchors the first node at twelve o'clock,
 *   y = R·sin(θi − π/2)  which is what makes the ring read as a hierarchy
 *                        rather than a rotated scatter.
 *
 * Validation pulses travel node → centre. They are driven by `activeIds`, so a
 * pulse means a seat actually spoke — an always-on animation would look alive
 * while telling you nothing.
 */
export function AgentRoundTable({
  size = 300, activeIds = [],
}: { size?: number; activeIds?: string[] }) {
  const { data: agents } = usePoll<Agent[]>("/api/agents", 60000);
  const seats = (agents ?? []).filter((a) => a.id !== "chair");
  const chair = (agents ?? []).find((a) => a.id === "chair");
  const n = seats.length || 6;

  const R = size / 2 - 34;
  const cx = size / 2;
  const cy = size / 2;
  const step = (2 * Math.PI) / n;

  const nodes = seats.map((a, i) => {
    const angle = step * i - Math.PI / 2;
    return { agent: a, x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle) };
  });

  return (
    <div className="relative mx-auto" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="absolute inset-0">
        {nodes.map(({ agent, x, y }) => {
          const live = activeIds.includes(agent.id);
          return (
            <g key={agent.id}>
              <line x1={x} y1={y} x2={cx} y2={cy}
                    stroke={live ? "rgba(0,200,5,0.5)" : "rgba(255,255,255,0.10)"}
                    strokeWidth={live ? 1.6 : 1} />
              {live && (
                <motion.line
                  x1={x} y1={y} x2={cx} y2={cy}
                  stroke="#00c805" strokeWidth={2.4} strokeLinecap="round"
                  initial={{ pathLength: 0, opacity: 0.9 }}
                  animate={{ pathLength: 1, opacity: 0 }}
                  transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
                />
              )}
            </g>
          );
        })}
        <circle cx={cx} cy={cy} r={34} fill="rgba(45,82,243,0.13)" stroke="rgba(45,82,243,0.45)" />
      </svg>

      {/* Centre: the Chair, where consensus resolves into an order. */}
      <div className="absolute flex flex-col items-center justify-center text-center"
           style={{ left: cx - 34, top: cy - 34, width: 68, height: 68 }}>
        <div className="text-[17px] leading-none">{chair?.icon ?? "🏛"}</div>
        <div className="text-[8px] text-white/55 mt-1 leading-tight px-1">Trade<br/>Execution</div>
      </div>

      {nodes.map(({ agent, x, y }) => {
        const live = activeIds.includes(agent.id);
        return (
          <motion.div key={agent.id}
            className="absolute group"
            style={{ left: x - 24, top: y - 24, width: 48, height: 48 }}
            animate={live ? { scale: [1, 1.09, 1] } : { scale: 1 }}
            transition={live ? { duration: 1.1, repeat: Infinity } : undefined}>
            <div className={`w-12 h-12 rounded-full flex items-center justify-center text-[17px]
              border glass-plain ${live ? "border-hood-green/60 bg-hood-green/15" : "border-white/15 bg-white/[0.07]"}`}>
              {agent.icon}
            </div>
            <div className="absolute left-1/2 -translate-x-1/2 top-[52px] w-max max-w-[150px]
                            opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-20
                            glass-plain bg-black/75 border border-white/15 rounded-lg px-2.5 py-1.5">
              <div className="text-[10px] font-semibold text-white">{agent.name}</div>
              <div className="text-[9px] text-white/55 leading-snug mt-0.5">{agent.mandate}</div>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}
