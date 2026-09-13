import type { Step } from "../components/Preflight";

/**
 * The paper-trading preflight, as a pure function so it can be tested.
 *
 * Two of the three original steps were hardcoded `ok: true`. A ceremony that
 * cannot fail is worse than no ceremony: it trains the operator to click
 * through ceremonies, including the one at the end of the rule-#13 checklist,
 * which is the last human gate before real money.
 *
 * Every step here reads live state that is already on the wire. Any check that
 * cannot be computed is DELETED rather than softened into a decoration.
 */
export interface PreflightState {
  systemOn: boolean;
  paperAttached: boolean;
  engineOn: boolean;
  engineAuthed: boolean;
  venue: string;
}

export function paperPreflightSteps(s: PreflightState): Step[] {
  return [
    {
      label: "Project धन is running",
      ok: s.systemOn,
      detail: s.systemOn ? "the scheduler is cycling"
                         : "start the system from the top-right control",
    },
    {
      label: `A paper venue is attached for ${s.venue}`,
      ok: s.paperAttached,
      detail: s.paperAttached ? "orders will route to the simulated adapter"
                              : "no paper adapter is registered — nothing would take the order",
    },
    {
      // Not required: paper fills are simulated either way. It changes what the
      // QUOTES are worth, which is the whole reason the record means anything.
      label: "Quotes are live from the venue",
      ok: s.engineOn && s.engineAuthed,
      required: false,
      detail: s.engineOn && s.engineAuthed
        ? "fills are simulated against real prices"
        : "engine off — fills will be simulated against provider prices, which is a weaker record",
    },
  ];
}
