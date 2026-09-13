import { useMemo } from "react";
import { createAvatar } from "@dicebear/core";
import { notionists } from "@dicebear/collection";

/**
 * Deterministic face for a seat.
 *
 * DiceBear is used as a LOCAL generator (@dicebear/core, MIT; the notionists
 * style is CC0 by Zoish) rather than through api.dicebear.com. Same library,
 * same seeds, same faces — but a dashboard bound to 127.0.0.1 that watches a
 * brokerage account should not be announcing itself to a third-party host on
 * every render, and the ring should still draw with the network down.
 *
 * Only `seed` reaches the generator. The advisory against this package is SVG
 * injection through `rotate`/`fontSize`/`fontWeight`; none of those are exposed
 * here, and there is nothing user-controlled to put in them.
 */
export function Avatar({ seed, size = 40, className = "" }: {
  seed: string; size?: number; className?: string;
}) {
  const uri = useMemo(() => {
    try {
      return createAvatar(notionists, { seed, radius: 50 }).toDataUri();
    } catch {
      return null;      // a missing face must not take the ring down with it
    }
  }, [seed]);

  if (!uri) {
    return (
      <span className={`inline-block rounded-full bg-white/10 ${className}`}
            style={{ width: size, height: size }} aria-hidden />
    );
  }
  return (
    <img src={uri} alt="" aria-hidden width={size} height={size}
         className={`rounded-full select-none ${className}`} draggable={false} />
  );
}
