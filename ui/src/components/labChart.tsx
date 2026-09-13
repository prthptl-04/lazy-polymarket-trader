import {
  Area, AreaChart, CartesianGrid, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Empty } from "./primitives";

/**
 * The simulation curve.
 *
 * Amber, drawn rather than faded in, with the newest point pulsing while the
 * engine is live — the one animation on this page that carries information:
 * it marks where the data actually ends, which on a curve that grows by one
 * point every few minutes is otherwise invisible.
 *
 * The pulse stops when the engine stops. An idle engine that looks like it is
 * ingesting is the same class of lie as a fabricated number.
 */
export function LabCurve({ values, live = false, height = "100%", label }: {
  values: number[]; live?: boolean; height?: number | string; label?: string;
}) {
  if (values.length < 2) {
    return (
      <div style={{ height }} className="flex items-center justify-center">
        <Empty title="Not enough history to plot."
               hint="The curve starts once a simulated position closes — an open position has an opinion about itself, a closed one has a result." />
      </div>
    );
  }

  const data = values.map((v, i) => ({ i, v }));
  const last = data[data.length - 1];

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 14, right: 16, bottom: 0, left: -14 }}>
        <defs>
          <linearGradient id="lab-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f59e0b" stopOpacity={0.28} />
            <stop offset="100%" stopColor="#f59e0b" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="lab-stroke" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#b45309" />
            <stop offset="70%" stopColor="#f59e0b" />
            <stop offset="100%" stopColor="#fb923c" />
          </linearGradient>
        </defs>
        {/* A faint grid, not a blank field: this is a terminal, and a reader
            should be able to judge a level without hovering. */}
        <CartesianGrid stroke="rgba(148,163,184,0.10)" strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="i" tick={{ fontSize: 9, fill: "#94a3b8" }}
               axisLine={false} tickLine={false} minTickGap={44} />
        <YAxis tick={{ fontSize: 9, fill: "#94a3b8" }} axisLine={false} tickLine={false}
               width={46} domain={["auto", "auto"]} />
        <Tooltip
          contentStyle={{ background: "rgba(15,23,42,0.94)", border: "1px solid rgba(255,255,255,0.1)",
                          borderRadius: 12, fontSize: 12 }}
          labelStyle={{ color: "#94a3b8", fontSize: 10 }}
          itemStyle={{ color: "#e2e8f0", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
          formatter={(v: number) => [`$${v.toFixed(2)}`, label ?? "equity"]}
          cursor={{ stroke: "rgba(245,158,11,0.35)" }} />
        {/* Drawn on: 900ms of path animation reads as ingestion rather than as
            a chart that was always there. */}
        <Area type="monotone" dataKey="v" stroke="url(#lab-stroke)" strokeWidth={2}
              fill="url(#lab-fill)" isAnimationActive animationDuration={900}
              animationEasing="ease-out" dot={false} />
        <ReferenceDot x={last.i} y={last.v} ifOverflow="extendDomain"
                      shape={(props: any) => (
                        <circle cx={props.cx} cy={props.cy} r={3} fill="#fb923c"
                                className={live ? "lab-tip" : undefined} />
                      )} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
