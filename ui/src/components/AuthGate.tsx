import { useEffect, useRef, useState } from "react";
import { getJSON, postJSON } from "../lib/api";

export interface AuthStatus {
  venue: string; authenticated: boolean; needs_auth: boolean;
  expiring_soon: boolean; seconds_remaining: number | null;
  reason: string; probed?: boolean;
}

type Phase = "idle" | "checking" | "authorising" | "waiting" | "failed";

/**
 * Authorisation, checked before the engine starts rather than discovered later.
 *
 * The failure this removes: the Robinhood token lapsed on a Monday morning, the
 * daemon correctly refused to open a browser, and the fund ran on with no venue
 * quotes for nine hours. Nothing checked, so nothing said.
 *
 * GO now probes the venue — actually opening the session, because a token file
 * can be present and well-formed while the server rejects it — and if it needs
 * authorising, opens the consent page in a popup and starts the engine once the
 * redirect lands.
 *
 * The popup is opened from the CLICK, synchronously. A window opened from an
 * await resolution is a pop-up blocker's definition of one, and the operator
 * would get a silently blocked tab and a button that appeared to do nothing.
 *
 * **The window is never closed from here.** Robinhood's consent does not end in
 * the browser: approving there sends a push to the phone app, and the flow
 * completes only once that is approved too. A tab closed on our schedule can
 * land in the middle of that — and on the failure paths it would take the
 * error message with it, leaving a button that did nothing for no stated
 * reason. The operator closes it; the callback page already says they can.
 */
export function useAuthGate(onReady: () => void | Promise<void>) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [detail, setDetail] = useState<string>("");
  const popup = useRef<Window | null>(null);
  const poll = useRef<number | null>(null);

  useEffect(() => () => { if (poll.current) window.clearInterval(poll.current); }, []);

  async function start(venue = "robinhood") {
    setPhase("checking");
    setDetail("Checking the venue session…");

    // A failed check must not block a paper run that needs no venue, so a
    // null answer starts the engine rather than refusing it.
    const status = await getJSON<AuthStatus>(`/api/auth/${venue}?probe=true`);
    if (!status || !status.needs_auth) {
      setPhase("idle");
      await onReady();
      return;
    }

    // Opened here, synchronously inside the user gesture that led here.
    popup.current = window.open("", "dhan-auth", "width=520,height=720");
    setPhase("authorising");
    setDetail(status.reason);

    const flow = await postJSON<{ authorize_url?: string | null; error?: string | null }>(
      `/api/auth/${venue}/begin`);
    if (!flow?.authorize_url) {
      // Left OPEN on purpose: if the venue put an error on that page, closing
      // it is destroying the only explanation the operator will get.
      setPhase("failed");
      setDetail(flow?.error || "the venue did not return an authorisation URL");
      return;
    }

    if (popup.current) popup.current.location.href = flow.authorize_url;
    else window.open(flow.authorize_url, "_blank", "noopener");

    setPhase("waiting");
    setDetail("Approve in the Robinhood window, then approve the push on your phone.");

    // Matches the server's callback window. Robinhood pushes to the phone app
    // after the browser consent, so this has to outlast finding the phone,
    // unlocking it and waiting for the notification.
    const deadline = Date.now() + 15 * 60 * 1000;
    let waited = 0;

    poll.current = window.setInterval(async () => {
      waited += 2;
      if (Date.now() > deadline) {
        if (poll.current) window.clearInterval(poll.current);
        setPhase("failed");
        setDetail("Timed out after 15 minutes. The window is still open — " +
                  "finish there and press GO again, or close it and retry.");
        return;
      }
      if (waited === 30) {
        setDetail("Still waiting. Robinhood sends a push to your phone app " +
                  "after the browser step — approve it there.");
      }
      const now = await getJSON<AuthStatus>(`/api/auth/${venue}`);
      if (!now || now.needs_auth) return;
      if (poll.current) window.clearInterval(poll.current);
      // Deliberately NOT closing the popup — see the note above.
      setPhase("idle");
      await onReady();
    }, 2000);
  }

  return { phase, detail, start,
           // Stops watching. Leaves the window alone: the operator may still
           // be mid-approval on their phone, and closing it would end a flow
           // that was about to succeed.
           cancel: () => {
             if (poll.current) window.clearInterval(poll.current);
             setPhase("idle");
           } };
}

/** The banner shown while the gate is working. */
export function AuthGateBanner({ phase, detail, onCancel }: {
  phase: Phase; detail: string; onCancel: () => void;
}) {
  if (phase === "idle") return null;
  const bad = phase === "failed";
  return (
    <div className={`flex items-center gap-3 px-3 py-2 rounded-xl border text-[11.5px] ${
      bad ? "border-red-400/30 bg-red-400/[0.07] text-red-300"
          : "border-amber-400/25 bg-amber-400/[0.06] text-amber-200/90"}`}>
      <span className="font-semibold">
        {phase === "checking" ? "Checking session"
          : phase === "authorising" ? "Opening Robinhood"
          : phase === "waiting" ? "Waiting for approval"
          : "Authorisation failed"}
      </span>
      <span className="opacity-80 flex-1 min-w-0 truncate">{detail}</span>
      <button onClick={onCancel}
        className="shrink-0 text-[11px] underline underline-offset-2 opacity-70 hover:opacity-100">
        Cancel
      </button>
    </div>
  );
}
