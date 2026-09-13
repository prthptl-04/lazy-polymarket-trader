import { useEffect } from "react";

/**
 * Polymarket dark mode.
 *
 * Scoped by a `data-venue-theme` attribute on <html> rather than by class
 * juggling inside components: the canvas is painted by `body`, which no view
 * owns, so the view has to reach the document to repaint it — and has to put
 * it back on unmount, or leaving the tab leaves the whole app navy.
 *
 * The surface colour is the slate composited OVER the canvas rather than laid
 * on it flat. #4a6572 at full strength is not "slightly lighter" than #0d1b2a,
 * it is four times its luminance; at 0.22 it lands a step above the canvas,
 * which is what reads as elevation without a shadow.
 */

export type PolymarketVariant = "midnight" | "navy";

export interface VenuePalette {
  canvas: string;       // page ground
  surface: string;      // GlassCard, composited over canvas
  surfaceRgb: string;   // the raw slate, for alpha blending in CSS
  border: string;
  heading: string;      // primary headings + balances
  muted: string;        // timestamps, agent names
  accent: string;       // Polymarket blue
  accentEdge: string;   // lighter blue: a 1px border of `accent` on `surface`
}                       // sits under the 3:1 UI-component floor.

export const ACCENT = "#2d52f3";
export const ACCENT_EDGE = "#4f74ff";
export const HEADING = "#F5F5F5";
export const MUTED = "#E0E0E0";

export const PALETTES: Record<PolymarketVariant, VenuePalette> = {
  // Deep midnight blue — closest to the live Polymarket book.
  midnight: {
    canvas: "#0d1b2a", surface: "rgba(74, 101, 114, 0.22)", surfaceRgb: "74 101 114",
    border: "rgba(74, 101, 114, 0.45)",
    heading: HEADING, muted: MUTED, accent: ACCENT, accentEdge: ACCENT_EDGE,
  },
  // Dark navy alternative.
  navy: {
    canvas: "#0A0A2E", surface: "rgba(26, 26, 62, 0.85)", surfaceRgb: "26 26 62",
    border: "rgba(90, 90, 150, 0.40)",
    heading: HEADING, muted: MUTED, accent: ACCENT, accentEdge: ACCENT_EDGE,
  },
};

/**
 * Paint the document in the Polymarket palette while `active`.
 *
 * The previous inline background is captured and restored, so this composes
 * with whatever else has painted the page instead of assuming it owns it.
 */
export function usePolymarketTheme(active: boolean, variant: PolymarketVariant = "midnight") {
  useEffect(() => {
    if (!active) return;
    const root = document.documentElement;
    const p = PALETTES[variant];
    const previousBackground = document.body.style.background;

    root.dataset.venueTheme = "polymarket";
    for (const [k, v] of Object.entries({
      "--pm-canvas": p.canvas, "--pm-surface": p.surface, "--pm-surface-rgb": p.surfaceRgb,
      "--pm-border": p.border, "--pm-heading": p.heading, "--pm-muted": p.muted,
      "--pm-accent": p.accent, "--pm-accent-edge": p.accentEdge,
    })) root.style.setProperty(k, v);
    // The base stylesheet paints body with a fixed multi-stop gradient; it has
    // to be overridden inline, not merely re-declared.
    document.body.style.background = p.canvas;

    return () => {
      delete root.dataset.venueTheme;
      document.body.style.background = previousBackground;
    };
  }, [active, variant]);

  return PALETTES[variant];
}
