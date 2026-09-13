import { useMemo } from "react";

/**
 * P&L-reactive page tint.
 *
 * The ramp is white → green on profit, white → red on loss, interpolated
 * channel-by-channel in sRGB so every intermediate value is a real colour
 * rather than a stepped bucket.
 *
 * Two things this does NOT do by eye:
 *
 * **It scales gains and losses separately.** A +$40 target and a −$18 stop are
 * not symmetric, and dividing both by the same number would paint a trade that
 * is halfway to its stop the same shade as one halfway to its target.
 *
 * **It measures contrast rather than assuming it.** Which ink is readable is a
 * function of the colour actually behind the text — which, inside a frosted
 * card, is the tint composited under the card's white veil, not the tint
 * itself. `readableInk` computes WCAG 2.1 relative luminance and picks the
 * winner, so changing the palette or the veil re-decides the ink instead of
 * silently dropping below 4.5:1.
 */

export interface Rgb { r: number; g: number; b: number }

export const NEUTRAL: Rgb = { r: 255, g: 255, b: 255 };
export const MAX_PROFIT: Rgb = { r: 0, g: 200, b: 5 };
export const MAX_LOSS: Rgb = { r: 255, g: 51, b: 51 };

/** Dark gray ink, and its white counterpart. `readableInk` chooses between them. */
export const INK_DARK: Rgb = { r: 26, g: 29, b: 35 };
export const INK_LIGHT: Rgb = { r: 255, g: 255, b: 255 };

/** How much white sits between the tint and the text inside a GlassCard. Body
 *  text has to clear 4.5:1 against THIS, not against the raw tint. */
export const CARD_VEIL = 0.86;

export const WCAG_BODY_TEXT = 4.5;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * Where this trade sits between its stop (−100) and its target (+100).
 * A missing or zero limit yields 0: an unbounded trade has no percentage to be.
 */
export function pnlPercent(
  currentPnL: number,
  maxExpectedPnL: number,
  maxExpectedLoss: number,
): number {
  if (!Number.isFinite(currentPnL) || currentPnL === 0) return 0;
  // The loss limit is accepted signed or unsigned — callers disagree about
  // whether "max loss" is -18 or 18, and both mean the same thing.
  const limit = Math.abs(currentPnL > 0 ? maxExpectedPnL : maxExpectedLoss);
  if (!Number.isFinite(limit) || limit === 0) return 0;
  return clamp((currentPnL / limit) * 100, -100, 100);
}

const mix = (a: number, b: number, t: number) => Math.round(a + (b - a) * t);

/** Interpolate the ramp at `pct` ∈ [−100, 100]. */
export function interpolate(pct: number, neutral: Rgb = NEUTRAL): Rgb {
  const p = clamp(pct, -100, 100);
  const end = p >= 0 ? MAX_PROFIT : MAX_LOSS;
  const t = Math.abs(p) / 100;
  return { r: mix(neutral.r, end.r, t), g: mix(neutral.g, end.g, t), b: mix(neutral.b, end.b, t) };
}

export const toCss = (c: Rgb) => `rgb(${c.r}, ${c.g}, ${c.b})`;

/** Flatten a translucent white veil over a colour — what the eye actually sees. */
export function composite(base: Rgb, over: Rgb, alpha: number): Rgb {
  const a = clamp(alpha, 0, 1);
  return {
    r: Math.round(base.r * (1 - a) + over.r * a),
    g: Math.round(base.g * (1 - a) + over.g * a),
    b: Math.round(base.b * (1 - a) + over.b * a),
  };
}

/** WCAG 2.1 relative luminance. */
export function relativeLuminance({ r, g, b }: Rgb): number {
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Pick the ink with more contrast against `bg`, and report what it got. */
export function readableInk(bg: Rgb): {
  ink: Rgb; surface: "light" | "dark"; contrast: number; passesBodyText: boolean;
} {
  const dark = contrastRatio(INK_DARK, bg);
  const light = contrastRatio(INK_LIGHT, bg);
  const useDark = dark >= light;
  const contrast = useDark ? dark : light;
  return {
    ink: useDark ? INK_DARK : INK_LIGHT,
    // "light" means a LIGHT GROUND taking dark ink — it is the surface being
    // described, not the ink.
    surface: useDark ? "light" : "dark",
    contrast,
    passesBodyText: contrast >= WCAG_BODY_TEXT,
  };
}

export interface DynamicBackground {
  pct: number;
  rgb: Rgb;
  /** Feed straight into `style={{ backgroundColor }}`. */
  color: string;
  /** The composited card surface the ink was chosen against. */
  cardColor: string;
  ink: string;
  inkRgb: string;          // "26 29 35", for rgb(var(--x) / <alpha>)
  surface: "light" | "dark";
  contrast: number;
  passesBodyText: boolean;
  /** Wrapper style, transition included. */
  style: React.CSSProperties;
}

export function useDynamicBackground(
  currentPnL: number | null | undefined,
  maxExpectedPnL: number | null | undefined,
  maxExpectedLoss: number | null | undefined,
  options: { neutral?: Rgb; veil?: number } = {},
): DynamicBackground {
  const { neutral = NEUTRAL, veil = CARD_VEIL } = options;
  return useMemo(() => {
    const pct = pnlPercent(currentPnL ?? 0, maxExpectedPnL ?? 0, maxExpectedLoss ?? 0);
    const rgb = interpolate(pct, neutral);
    const card = composite(rgb, { r: 255, g: 255, b: 255 }, veil);
    const { ink, surface, contrast, passesBodyText } = readableInk(card);
    return {
      pct, rgb, color: toCss(rgb), cardColor: toCss(card),
      ink: toCss(ink), inkRgb: `${ink.r} ${ink.g} ${ink.b}`,
      surface, contrast, passesBodyText,
      style: {
        backgroundColor: toCss(rgb),
        transition: "background-color 0.5s ease-in-out",
        // Consumed by the [data-surface="light"] rules in index.css.
        ["--dhan-ink" as string]: toCss(ink),
        ["--dhan-ink-rgb" as string]: `${ink.r} ${ink.g} ${ink.b}`,
        ["--dhan-card" as string]: `rgba(255, 255, 255, ${veil})`,
      } as React.CSSProperties,
    };
  }, [currentPnL, maxExpectedPnL, maxExpectedLoss, neutral, veil]);
}
