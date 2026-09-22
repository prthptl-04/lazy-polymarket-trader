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
      popup.current?.close();
      setPhase("failed");
      setDetail(flow?.error || "the venue did not return an authorisation URL");
      return;
    }

    if (popup.current) popup.current.location.href = flow.authorize_url;
    else window.open(flow.authorize_url, "_blank", "noopener");

    setPhase("waiting");
    setDetail("Approve in the Robinhood window, then this starts the engine.");

    poll.current = window.setInterval(async () => {
      const now = await getJSON<AuthStatus>(`/api/auth/${venue}`);
      if (!now || now.needs_auth) return;   // keep waiting; the flow times out
      if (poll.current) window.clearInterval(poll.current);
      popup.current?.close();
      setPhase("idle");
      await onReady();
    }, 2000);
  }

  return { phase, detail, start,
           cancel: () => {
             if (poll.current) window.clearInterval(poll.current);
             popup.current?.close();
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
