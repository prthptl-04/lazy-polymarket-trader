/**
 * The SVG filter pipeline behind the Liquid Glass surfaces.
 *
 * CSS `backdrop-filter: blur()` gives frosted glass but not *refraction* —
 * light bending around a curved edge. Native Apple surfaces do that with
 * hardware shaders that can see their surroundings; the closest a browser gets
 * is an SVG filter chain referenced from backdrop-filter.
 *
 * Rendered once at the root so every surface can reference `url(#liquid-glass)`.
 * It must stay in the DOM (a filter referenced by a removed node stops
 * resolving) but must never be interactive or occupy layout.
 */
export function LiquidGlassProvider() {
  return (
    <svg
      aria-hidden="true"
      className="absolute w-0 h-0 pointer-events-none"
      style={{ position: "absolute", width: 0, height: 0 }}
    >
      <defs>
        <filter id="liquid-glass" x="-20%" y="-20%" width="140%" height="140%">
          {/* Soften the backdrop before displacing it, so the distortion reads
              as thick glass rather than as noise. */}
          <feGaussianBlur in="SourceGraphic" stdDeviation="15" result="blur" />

          {/* Turbulence drives the displacement map. A low baseFrequency gives
              slow, liquid undulation; higher values look like frosted plastic. */}
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.008 0.012"
            numOctaves="2"
            seed="7"
            result="noise"
          />
          <feDisplacementMap
            in="blur"
            in2="noise"
            scale="18"
            xChannelSelector="R"
            yChannelSelector="G"
            result="displaced"
          />

          {/* Lift the alpha channel so the surface gains body. Without this the
              displaced layer reads as washed out against a dark ground. */}
          <feColorMatrix
            in="displaced"
            type="matrix"
            values="1 0 0 0 0
                    0 1 0 0 0
                    0 0 1 0 0
                    0 0 0 1.15 0"
            result="tinted"
          />

          {/* Specular rim: a blurred, offset copy composited only where it
              overlaps the edge, which is what sells the curvature. */}
          <feGaussianBlur in="tinted" stdDeviation="6" result="rimBlur" />
          <feOffset in="rimBlur" dx="0" dy="-1" result="rimShift" />
          <feComposite in="rimShift" in2="tinted" operator="atop" result="rim" />

          {/* Merge last so text drawn above the surface stays legible — the
              refraction applies to the BACKDROP, never to the content. */}
          <feMerge>
            <feMergeNode in="tinted" />
            <feMergeNode in="rim" />
          </feMerge>
        </filter>
      </defs>
    </svg>
  );
}
