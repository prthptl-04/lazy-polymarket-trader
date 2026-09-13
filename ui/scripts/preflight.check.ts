/**
 * Runnable check for the preflight: `npm run check:preflight`.
 *
 * The thing being guarded is not a rendering detail — it is that a step can
 * FAIL. Two of the original three were hardcoded `ok: true`, which is a
 * ceremony that trains the operator to click through ceremonies.
 */
import { strict as assert } from "node:assert";
import { paperPreflightSteps } from "../src/lib/preflight.ts";

const NOTHING = { systemOn: false, paperAttached: false, engineOn: false,
                  engineAuthed: false, venue: "robinhood" };
const EVERYTHING = { systemOn: true, paperAttached: true, engineOn: true,
                     engineAuthed: true, venue: "robinhood" };

// Every step must be capable of failing.
assert.ok(paperPreflightSteps(NOTHING).every((s) => !s.ok),
  "a step that cannot fail is a decoration");
assert.ok(paperPreflightSteps(EVERYTHING).every((s) => s.ok));

// The required steps are the ones that would make the run a lie.
const required = paperPreflightSteps(NOTHING).filter((s) => s.required !== false);
assert.equal(required.length, 2, "system running and a paper adapter attached");

// Every step says something about the state it read — a bare tick teaches nothing.
assert.ok(paperPreflightSteps(NOTHING).every((s) => (s.detail ?? "").length > 10));

// A stopped system must halt the sequence: the first required step is the one
// that fails, so the action never fires.
const blocked = paperPreflightSteps({ ...EVERYTHING, systemOn: false });
assert.equal(blocked.findIndex((s) => s.required !== false && !s.ok), 0);

console.log("preflight: every step can fail, and says why");
