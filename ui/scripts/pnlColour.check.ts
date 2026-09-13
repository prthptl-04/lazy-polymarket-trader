/**
 * Runnable check for the P&L colour ramp: `npm run check:colour`.
 *
 * The point is not that the maths compiles — it is that every colour this
 * dashboard can paint still clears WCAG body text. A palette tweak that
 * quietly drops a label to 3:1 fails here rather than in front of someone
 * reading a loss.
 */
import { strict as assert } from "node:assert";
import {
  CARD_VEIL, INK_DARK, MAX_LOSS, MAX_PROFIT, NEUTRAL, WCAG_BODY_TEXT,
  composite, contrastRatio, interpolate, pnlPercent, readableInk,
} from "../src/lib/useDynamicBackground.ts";

// ---- percentage ----
assert.equal(pnlPercent(0, 40, 18), 0);
assert.equal(pnlPercent(40, 40, 18), 100);
assert.equal(pnlPercent(-18, 40, 18), -100);
assert.equal(pnlPercent(-18, 40, -18), -100, "a signed loss limit means the same thing");
// Gains and losses scale against their OWN limit.
assert.equal(pnlPercent(20, 40, 18), 50);
assert.equal(pnlPercent(-9, 40, 18), -50);
// Beyond the plan is still on the ramp, not off it.
assert.equal(pnlPercent(999, 40, 18), 100);
assert.equal(pnlPercent(-999, 40, 18), -100);
// No limit to be a percentage of.
assert.equal(pnlPercent(25, 0, 0), 0);
assert.equal(pnlPercent(NaN, 40, 18), 0);

// ---- interpolation ----
assert.deepEqual(interpolate(0), NEUTRAL);
assert.deepEqual(interpolate(100), MAX_PROFIT);
assert.deepEqual(interpolate(-100), MAX_LOSS);
// +50% is a soft mint: halfway down green's red and blue channels.
assert.deepEqual(interpolate(50), { r: 128, g: 228, b: 130 });
// Monotone: every step toward profit removes red.
let previous = 256;
for (let p = 0; p <= 100; p += 5) {
  const { r } = interpolate(p);
  assert.ok(r <= previous, `red channel rose at ${p}%`);
  previous = r;
}

// ---- contrast, across the whole ramp ----
for (let p = -100; p <= 100; p += 5) {
  const card = composite(interpolate(p), { r: 255, g: 255, b: 255 }, CARD_VEIL);
  const { contrast, passesBodyText, surface } = readableInk(card);
  assert.ok(passesBodyText, `${p}% gives ${contrast.toFixed(2)}:1 inside a card`);
  assert.equal(surface, "light", `${p}% should take dark ink`);
}

// The raw tint, with no card veil, still has to carry text at the extremes.
for (const bg of [NEUTRAL, MAX_PROFIT, MAX_LOSS]) {
  assert.ok(readableInk(bg).contrast >= WCAG_BODY_TEXT,
    `bare ${JSON.stringify(bg)} fails body text`);
}

// ---- light-mode accents (index.css must match these) ----
const worstCard = composite(MAX_PROFIT, { r: 255, g: 255, b: 255 }, CARD_VEIL);
for (const [name, hex] of Object.entries({
  green: { r: 10, g: 125, b: 20 }, red: { r: 200, g: 30, b: 30 }, amber: { r: 138, g: 82, b: 6 },
})) {
  const c = contrastRatio(hex, worstCard);
  assert.ok(c >= WCAG_BODY_TEXT, `${name} accent is ${c.toFixed(2)}:1 on a mint card`);
}

// The faintest text bucket in index.css is 0.66 alpha ink; it has to clear the
// bar too, or every label on the dashboard is decorative.
const faint = composite(worstCard, INK_DARK, 0.66);
assert.ok(contrastRatio(faint, worstCard) >= WCAG_BODY_TEXT,
  `faintest label bucket is ${contrastRatio(faint, worstCard).toFixed(2)}:1`);

console.log("pnl colour ramp: all checks pass");
