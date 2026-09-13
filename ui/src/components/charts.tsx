import {
  Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceDot,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Empty, money } from "./primitives";

/** One point is not a line. Recharts happily renders a lone dot in an empty
 *  frame, which reads as a broken chart rather than as "nothing has closed
 *  yet" — so say which it is. */
function tooShort(values: number[], height: number) {
  if (values.length >= 2) return null;
  return (
    <div style={{ height }} className="flex items-center justify-center">
      <Empty title="Not enough history to plot."
             hint="The curve starts once a position closes — an open position has an opinion about itself, a closed one has a result." />
    </div>
  );
}

export interface Marker { x: number; y: number; label: string; tone?: "entry" | "stop" | "target" }

/** Glass tooltip. Recharts renders it inside the chart's SVG wrapper, which
 *  sits inside a `overflow-hidden` glass card — so it is kept compact and
 *  inside the plot area rather than escaping it. */
function GlassTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="glass-plain bg-black/60 border border-white/15 rounded-xl px-3 py-2 shadow-glass">
      <div className="text-[10px] text-white/40 mb-0.5">#{label}</div>
      <div className="font-mono text-[13px] text-white">{money(payload[0].value)}</div>
    </div>
  );
}

/**
 * Trade markers.
 *
 * `ReferenceDot` is Cartesian-aware: it takes DATA values and Recharts resolves
 * the pixel position itself, so a marker stays glued to its point through
 * resizes and data growth. Hand-placed SVG circles drift the moment the axis
 * domain changes — which on a live chart is constantly.
 */
function markerShape(tone: Marker["tone"], label: string, accent = "#2d52f3") {
  // Stop and target are semantic and fixed; an entry marker belongs to whatever
  // venue owns the chart, so it takes the series colour.
  const colour = tone === "stop" ? "#f87171" : tone === "target" ? "#00c805" : accent;
  return (props: any) => {
    const { cx, cy } = props;               // pixel coords Recharts computed
    if (cx == null || cy == null) return <g />;
    const flip = cx > 260;                  // keep the flag inside the plot
    return (
      <g>
        <circle cx={cx} cy={cy} r={9} fill={colour} opacity={0.18} />
        <circle cx={cx} cy={cy} r={4} fill={colour} stroke="#fff" strokeWidth={1.2} />
        <line x1={cx} y1={cy} x2={flip ? cx - 10 : cx + 10} y2={cy - 14}
              stroke={colour} strokeWidth={1} opacity={0.6} />
        <text x={flip ? cx - 13 : cx + 13} y={cy - 16}
              textAnchor={flip ? "end" : "start"}
              fontSize={9} fill={colour} fontWeight={600}>{label}</text>
      </g>
    );
  };
}

export function EquityArea({
  values, colour = "#2d52f3", markers = [], height = 190,
}: { values: number[]; colour?: string; markers?: Marker[]; height?: number }) {
  const short = tooShort(values, height);
  if (short) return short;
  const data = values.map((v, i) => ({ i, v }));
  const id = `grad-${colour.replace("#", "")}`;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 12, right: 10, bottom: 0, left: -18 }}>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={colour} stopOpacity={0.4} />
            <stop offset="100%" stopColor={colour} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
        <XAxis dataKey="i" tick={{ fontSize: 9, fill: "rgba(255,255,255,0.25)" }}
               axisLine={false} tickLine={false} minTickGap={40} />
        <YAxis tick={{ fontSize: 9, fill: "rgba(255,255,255,0.25)" }}
               axisLine={false} tickLine={false} width={48} domain={["auto", "auto"]} />
        <Tooltip content={<GlassTooltip />} cursor={{ stroke: "rgba(255,255,255,0.15)" }} />
        <Area type="monotone" dataKey="v" stroke={colour} strokeWidth={2}
              fill={`url(#${id})`} isAnimationActive={false} />
        {markers.map((m, k) => (
          <ReferenceDot key={k} x={m.x} y={m.y} ifOverflow="extendDomain"
                        shape={markerShape(m.tone, m.label, colour)} />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Robinhood's signature: a stark unfilled neon stroke. */
export function Sparkline({
  values, colour = "#00c805", markers = [], height = 230,
}: { values: number[]; colour?: string; markers?: Marker[]; height?: number }) {
  const short = tooShort(values, height);
  if (short) return short;
  const data = values.map((v, i) => ({ i, v }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 14, right: 10, bottom: 0, left: -18 }}>
        <XAxis dataKey="i" hide />
        <YAxis domain={["auto", "auto"]} hide />
        <Tooltip content={<GlassTooltip />} cursor={{ stroke: "rgba(255,255,255,0.12)" }} />
        <Line type="monotone" dataKey="v" stroke={colour} strokeWidth={2}
              dot={false} isAnimationActive={false} />
        {markers.map((m, k) => (
          <ReferenceDot key={k} x={m.x} y={m.y} ifOverflow="extendDomain"
                        shape={markerShape(m.tone, m.label, colour)} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
