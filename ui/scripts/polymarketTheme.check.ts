/**
 * Runnable check for the Polymarket palette: `npm run check:theme`.
 *
 * Both variants have to clear WCAG on every pairing the view can produce —
 * including the one that is easy to get wrong, the accent used as a control
 * boundary, where the floor is 3:1 rather than 4.5:1.
 */
import { strict as assert } from "node:assert";
import { contrastRatio, type Rgb } from "../src/lib/useDynamicBackground.ts";
import { ACCENT, ACCENT_EDGE, HEADING, MUTED, PALETTES } from "../src/lib/polymarketTheme.ts";

const hex = (h: string): Rgb => ({
  r: parseInt(h.slice(1, 3), 16), g: parseInt(h.slice(3, 5), 16), b: parseInt(h.slice(5, 7), 16),
});
const over = (base: Rgb, o: Rgb, a: number): Rgb => ({
  r: Math.round(base.r * (1 - a) + o.r * a),
  g: Math.round(base.g * (1 - a) + o.g * a),
  b: Math.round(base.b * (1 - a) + o.b * a),
});
/** "rgba(74, 101, 114, 0.22)" → the colour it composites to over `canvas`. */
function surfaceOver(canvas: Rgb, css: string): Rgb {
  const [r, g, b, a] = css.match(/[\d.]+/g)!.map(Number);
  return over(canvas, { r, g, b }, a);
}

const BODY = 4.5, UI_COMPONENT = 3.0;

for (const [name, p] of Object.entries(PALETTES)) {
  const canvas = hex(p.canvas);
  const surface = surfaceOver(canvas, p.surface);

  // The surface has to be lighter than the ground, or "depth" is a claim the
  // pixels do not support.
  assert.ok(contrastRatio(surface, canvas) > 1.0, `${name}: surface does not separate from canvas`);

  for (const ground of [canvas, surface]) {
    for (const [label, ink] of [["heading", HEADING], ["muted", MUTED]] as const) {
      const c = contrastRatio(hex(ink), ground);
      assert.ok(c >= BODY, `${name}: ${label} is ${c.toFixed(2)}:1`);
    }
    // The faintest typography bucket in index.css is the muted gray at 0.72.
    const faint = over(ground, hex(MUTED), 0.72);
    const c = contrastRatio(faint, ground);
    assert.ok(c >= BODY, `${name}: faintest label bucket is ${c.toFixed(2)}:1`);
  }

  // A filled accent button carries off-white text.
  const onAccent = contrastRatio(hex(HEADING), hex(ACCENT));
  assert.ok(onAccent >= BODY, `${name}: heading on accent is ${onAccent.toFixed(2)}:1`);

  // The control's boundary must be perceivable against the card. This is why
  // the border is ACCENT_EDGE and not ACCENT.
  const rawEdge = contrastRatio(hex(ACCENT), surface);
  const edge = contrastRatio(hex(ACCENT_EDGE), surface);
  assert.ok(edge >= UI_COMPONENT,
    `${name}: control edge is ${edge.toFixed(2)}:1 (raw accent would be ${rawEdge.toFixed(2)}:1)`);

  console.log(
    `${name.padEnd(9)} canvas ${p.canvas}  surface→rgb(${surface.r},${surface.g},${surface.b})  ` +
    `heading ${contrastRatio(hex(HEADING), surface).toFixed(2)}:1  ` +
    `muted ${contrastRatio(hex(MUTED), surface).toFixed(2)}:1  edge ${edge.toFixed(2)}:1`);
}

console.log("polymarket palette: all checks pass");
