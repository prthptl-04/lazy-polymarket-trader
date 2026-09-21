import { useEffect, useRef, useState } from "react";
import {
  Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, YAxis,
} from "recharts";
import { LIVE, usePoll, getJSON, type Feed } from "../lib/api";

/**
 * A price chart that moves, in the shape a broker's app draws one.
 *
 * The venue panels were charting the fund's EQUITY CURVE, which is a single
 * point until something closes — so every chart on the page rendered as an
 * empty frame and read as broken. Price history was available the whole time
 * and unused.
 *
 * Three things a broker's chart does that a generic line chart does not, and
 * each of them is why this is a component rather than a call to <Sparkline>:
 *
 * **It is coloured by the session's direction, not by the venue.** Green when
 * the last price is above where the window opened, red below. The colour is
 * information, so it is derived from the data rather than passed in.
 *
 * **The baseline is drawn.** A price chart without the open is just a wiggle;
 * with it, every point is immediately readable as up or down on the day.
 *
 * **The live price extends the history.** Candles give the shape, the 1Hz feed
 * gives the last point, and appending rather than refetching is what makes it
 * tick instead of flicker.
 */

interface Candles {
  symbol: string; closes: number[];
  entry?: number | null; stop?: number | null; reason?: string;
}

const UP = "#00c805";
const DOWN = "#ff4d4d";

/** How many live ticks to keep on the tail before the history is refetched.
 *  Enough to show the last few minutes of movement; not so many that the
 *  chart's shape is dominated by one poll interval's worth of noise. */
const MAX_LIVE_TAIL = 180;

export function PriceChart({
  symbol, height = 220, showPlan = true, className = "",
}: { symbol: string; height?: number | string; showPlan?: boolean; className?: string }) {
  const [candles, setCandles] = useState<Candles | null>(null);
  const [tail, setTail] = useState<number[]>([]);
  const lastTick = useRef<number | null>(null);

  // History is slow-moving and expensive; the live tail is neither. Fetching
  // the bars once and appending to them is the difference between a chart that
  // ticks and one that flickers as it redraws from scratch every second.
  useEffect(() => {
    let alive = true;
    setTail([]); lastTick.current = null;
    void getJSON<Candles>(`/api/candles?symbol=${encodeURIComponent(symbol)}&lookback=120`)
      .then(c => { if (alive) setCandles(c); });
    const id = window.setInterval(() => {
      void getJSON<Candles>(`/api/candles?symbol=${encodeURIComponent(symbol)}&lookback=120`)
        .then(c => { if (alive && c) { setCandles(c); setTail([]); } });
    }, 60_000);
    return () => { alive = false; window.clearInterval(id); };
  }, [symbol]);

  const { data: feed } = usePoll<Feed[]>("/api/feeds?venue=robinhood", LIVE);
  const live = feed?.find(f => f.symbol === symbol);
  const mark = live?.last ?? (live?.bid != null && live?.ask != null
    ? (live.bid + live.ask) / 2 : null);

  useEffect(() => {
    if (mark == null || mark === lastTick.current) return;
    lastTick.current = mark;
    setTail(t => [...t, mark].slice(-MAX_LIVE_TAIL));
  }, [mark]);

  const closes = candles?.closes ?? [];
  const series = [...closes, ...tail];

  if (series.length < 2) {
    return (
      <div style={{ height }} className={`flex items-center justify-center ${className}`}>
        <div className="text-[11px] text-white/35 text-center px-4">
          {candles?.reason ?? "loading price history…"}
        </div>
      </div>
    );
  }

  const open = series[0];
  const last = series[series.length - 1];
  const up = last >= open;
  const colour = up ? UP : DOWN;
  const changePct = open ? ((last - open) / open) * 100 : 0;
  const data = series.map((v, i) => ({ i, v }));

  // A price axis that starts at zero wastes the whole panel on empty space and
  // flattens the move it exists to show.
  const lo = Math.min(...series, ...(showPlan && candles?.stop ? [candles.stop] : []));
  const hi = Math.max(...series, ...(showPlan && candles?.entry ? [candles.entry] : []));
  const pad = (hi - lo) * 0.08 || hi * 0.01;

  const gid = `px-${symbol.replace(/[^a-z0-9]/gi, "")}-${up ? "u" : "d"}`;

  return (
    <div className={className}>
      <div className="flex items-baseline gap-2 mb-1">
        <span className="font-mono text-[19px] tracking-tight" style={{ color: colour }}>
          {last.toLocaleString(undefined, { maximumFractionDigits: 2 })}
        </span>
        <span className="font-mono text-[12px]" style={{ color: colour }}>
          {changePct >= 0 ? "▲" : "▼"} {Math.abs(changePct).toFixed(2)}%
        </span>
        {tail.length > 0 && (
          <span className="text-[9px] uppercase tracking-[0.12em] text-white/30">live</span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 6, right: 6, bottom: 0, left: -20 }}>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colour} stopOpacity={0.35} />
              <stop offset="100%" stopColor={colour} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis domain={[lo - pad, hi + pad]} hide />
          {/* The open. Without it the line is a wiggle; with it, every point
              reads immediately as up or down on the window. */}
          <ReferenceLine y={open} stroke="#ffffff" strokeOpacity={0.18}
                         strokeDasharray="3 4" />
          {showPlan && candles?.entry != null && (
            <ReferenceLine y={candles.entry} stroke={UP} strokeOpacity={0.5}
                           strokeDasharray="2 3"
                           label={{ value: "entry", fill: UP, fontSize: 9, position: "insideLeft" }} />
          )}
          {showPlan && candles?.stop != null && (
            <ReferenceLine y={candles.stop} stroke={DOWN} strokeOpacity={0.6}
                           strokeDasharray="2 3"
                           label={{ value: "stop", fill: DOWN, fontSize: 9, position: "insideLeft" }} />
          )}
          <Tooltip
            contentStyle={{ background: "rgba(0,0,0,0.75)", border: "1px solid rgba(255,255,255,0.15)",
                            borderRadius: 12, fontSize: 11 }}
            labelFormatter={() => ""}
            formatter={(v: any) => [Number(v).toLocaleString(undefined,
              { maximumFractionDigits: 2 }), symbol]} />
          <Area type="monotone" dataKey="v" stroke={colour} strokeWidth={1.8}
                fill={`url(#${gid})`} isAnimationActive={false} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
