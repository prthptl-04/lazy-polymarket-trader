import { motion, type HTMLMotionProps } from "framer-motion";
import type { ReactNode } from "react";

export interface GlassCardProps extends Omit<HTMLMotionProps<"div">, "children"> {
  children?: ReactNode;
  className?: string;
  /** Use the SVG refraction filter. Off by default: feDisplacementMap is
   *  expensive, and a dashboard repainting charts every few seconds should not
   *  pay for it on every surface. Reserve it for hero panels. */
  liquid?: boolean;
  /** Disables the hover lift, for surfaces that are not interactive. */
  inert?: boolean;
}

/**
 * The base Liquid Glass surface.
 *
 * `overflow-hidden` is what keeps the rounded corners clipping the blur, but it
 * also clips any popover rendered inside — tooltips and dropdowns must go
 * through a portal to the body instead (see lib/portal).
 */
export function GlassCard({
  children, className = "", liquid = false, inert = false, ...rest
}: GlassCardProps) {
  return (
    <motion.div
      {...rest}
      className={[
        "bg-glass-white border border-glass-border",
        liquid ? "liquid-glass" : "glass-plain",
        "shadow-glass rounded-3xl overflow-hidden",
        className,
      ].join(" ")}
      style={{ boxShadow: "0 8px 32px 0 rgba(31,38,135,0.37), inset 0 1px 0 rgba(255,255,255,0.15)", ...rest.style }}
      whileHover={inert ? undefined : { scale: 1.01, backgroundColor: "rgba(255,255,255,0.12)" }}
      transition={{ type: "spring", stiffness: 400, damping: 30 }}
    >
      {children}
    </motion.div>
  );
}

/** Section label. Repeated on every panel, so it lives here rather than being
 *  re-typed with slightly different tracking each time. */
export function PanelTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between mb-3">
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-white/45">
        {children}
      </h2>
      {right}
    </div>
  );
}
