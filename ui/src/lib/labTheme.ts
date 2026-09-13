import { useEffect } from "react";

/**
 * The Simulation Lab.
 *
 * Deliberately NOT the venues' languages. Polymarket is midnight glass and
 * Robinhood is pitch black; a page where nothing is real has to say so at a
 * glance, before anyone reads a number on it. The signal is the ground and the
 * accent: a muted slate void rather than true black, and amber everywhere the
 * venues would have used their own colour.
 *
 * Muted, not black, for a structural reason — depth in a dark UI comes from
 * making foreground panels LIGHTER than the ground, and there is nothing
 * lighter than black to step up from. #0f172a leaves room above it.
 */

export const LAB_VOID = "#0f172a";
export const LAB_AMBER = "#f59e0b";
export const LAB_AMBER_HOT = "#fb923c";
export const LAB_INK = "#e2e8f0";
export const LAB_MUTED = "#94a3b8";

export function useSimulationLab(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const root = document.documentElement;
    const previousBackground = document.body.style.background;

    root.dataset.lab = "on";
    for (const [k, v] of Object.entries({
      "--lab-void": LAB_VOID, "--lab-amber": LAB_AMBER,
      "--lab-amber-hot": LAB_AMBER_HOT, "--lab-ink": LAB_INK, "--lab-muted": LAB_MUTED,
    })) root.style.setProperty(k, v);
    document.body.style.background = LAB_VOID;

    return () => {
      delete root.dataset.lab;
      document.body.style.background = previousBackground;
    };
  }, [active]);
}
