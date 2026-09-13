import { useEffect } from "react";

/**
 * Robinhood Gold: pitch black, flat, borderless, metallic.
 *
 * Same mechanism as the Polymarket theme — an attribute on <html> plus an
 * inline body background, restored on unmount — but the opposite instinct.
 * Polymarket keeps the glass; this strips it: no backdrop blur, no card
 * background, no radius, no shadow. Structure is carried by whitespace and by
 * hairline row rules, because a borderless card with no separators is a wall
 * of numbers.
 */

export const RH_BLACK = "#000000";
/** Robinhood Gold. Gains, active states and every interactive edge. */
export const RH_GOLD = "#FFD700";
export const RH_GOLD_DEEP = "#B8860B";     // rich gold — gradient tail
export const RH_CHAMPAGNE = "#D4AF37";     // muted gold — secondary/error text
export const RH_WHITE = "#FFFFFF";
/** Node outline from the spec. Decorative: 1.66:1 on black, so anything
 *  actually interactive takes the accent on hover/focus instead. */
export const RH_HAIRLINE = "#333333";

export function useRobinhoodTheme(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const root = document.documentElement;
    const previousBackground = document.body.style.background;

    root.dataset.venueTheme = "robinhood";
    for (const [k, v] of Object.entries({
      "--rh-black": RH_BLACK, "--rh-gold": RH_GOLD,
      "--rh-gold-deep": RH_GOLD_DEEP, "--rh-champagne": RH_CHAMPAGNE,
      "--rh-white": RH_WHITE, "--rh-hairline": RH_HAIRLINE,
    })) root.style.setProperty(k, v);
    document.body.style.background = RH_BLACK;

    return () => {
      delete root.dataset.venueTheme;
      document.body.style.background = previousBackground;
    };
  }, [active]);
}
