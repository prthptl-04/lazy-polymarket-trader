import { useEffect } from "react";

/**
 * Robinhood dark mode: pitch black, flat, borderless.
 *
 * Same mechanism as the Polymarket theme — an attribute on <html> plus an
 * inline body background, restored on unmount — but the opposite instinct.
 * Polymarket keeps the glass; this strips it: no backdrop blur, no card
 * background, no radius, no shadow. Structure is carried by whitespace and by
 * hairline row rules, because a borderless card with no separators is a wall
 * of numbers.
 */

export const RH_BLACK = "#000000";
export const RH_GREEN = "#00C805";
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
      "--rh-black": RH_BLACK, "--rh-green": RH_GREEN,
      "--rh-white": RH_WHITE, "--rh-hairline": RH_HAIRLINE,
    })) root.style.setProperty(k, v);
    document.body.style.background = RH_BLACK;

    return () => {
      delete root.dataset.venueTheme;
      document.body.style.background = previousBackground;
    };
  }, [active]);
}
