"use client";

/** Keeps an open instrument page current without a reload.
 *
 *  The backend collector writes validated observations to the database and this
 *  hook is told when they land, over Server-Sent Events. The frontend never
 *  talks to KASE itself: an event carries only a timestamp, and the panels then
 *  re-read the same API they would have read anyway. Nothing appears on screen
 *  that the API would not also serve.
 *
 *  This is a ten-minute public-web cadence, not a trading feed. The UI labels it
 *  as "last checked" rather than realtime, and `poll_seconds` from the server
 *  says how often the stream itself looks.
 *
 *  If EventSource is unavailable or the stream errors (a proxy that buffers, a
 *  serverless deployment that refuses long connections), the hook falls back to
 *  periodic revalidation so the page still catches up.
 */

import { useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";

import { API_URL } from "@/services/client";
import type { InstrumentKind } from "@/types/api";

/** How often the fallback re-reads when the stream is unavailable. */
const FALLBACK_POLL_MS = 120_000;

export type StreamState = {
  /** "stream" once connected, "polling" after a failure, "idle" before either. */
  transport: "idle" | "stream" | "polling";
  /** Server's newest stored moment for this instrument, as last announced. */
  lastUpdated: string | null;
  /** Increments on every update event, so callers can react to a change. */
  revision: number;
};

export function useInstrumentStream(
  kind: InstrumentKind,
  identifier: string | null,
): StreamState {
  const { mutate } = useSWRConfig();
  const [state, setState] = useState<StreamState>({
    transport: "idle",
    lastUpdated: null,
    revision: 0,
  });
  // Kept in a ref so the effect below never re-subscribes just to read it.
  const mutateRef = useRef(mutate);
  mutateRef.current = mutate;

  useEffect(() => {
    if (!identifier) return;

    /** Re-read every cached entry that belongs to this instrument.
     *
     *  SWR keys in this app are arrays whose second and third members are the
     *  kind and the identifier, so one predicate covers the chart, the series,
     *  the change feed and the card without naming each of them here. */
    const revalidate = () =>
      mutateRef.current(
        (key) =>
          Array.isArray(key) &&
          key.includes(kind) &&
          key.includes(identifier),
        undefined,
        { revalidate: true },
      );

    let source: EventSource | null = null;
    let fallback: ReturnType<typeof setInterval> | null = null;

    const startFallback = () => {
      if (fallback !== null) return;
      setState((prev) => ({ ...prev, transport: "polling" }));
      fallback = setInterval(() => {
        void revalidate();
      }, FALLBACK_POLL_MS);
    };

    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      startFallback();
      return () => {
        if (fallback !== null) clearInterval(fallback);
      };
    }

    const url = `${API_URL}/instruments/${encodeURIComponent(identifier)}/stream`;
    try {
      source = new EventSource(url);
    } catch {
      startFallback();
      return () => {
        if (fallback !== null) clearInterval(fallback);
      };
    }

    source.addEventListener("connected", (event) => {
      const data = parse(event);
      setState((prev) => ({
        ...prev,
        transport: "stream",
        lastUpdated: data?.last_updated ?? prev.lastUpdated,
      }));
    });

    source.addEventListener("update", (event) => {
      const data = parse(event);
      setState((prev) => ({
        transport: "stream",
        lastUpdated: data?.last_updated ?? prev.lastUpdated,
        revision: prev.revision + 1,
      }));
      void revalidate();
    });

    source.onerror = () => {
      // EventSource reconnects on its own; the fallback only covers the case
      // where it cannot succeed at all. Closing first avoids two live paths.
      if (source && source.readyState === EventSource.CLOSED) {
        startFallback();
      }
    };

    return () => {
      source?.close();
      if (fallback !== null) clearInterval(fallback);
    };
  }, [kind, identifier]);

  return state;
}

function parse(event: Event): { last_updated?: string | null } | null {
  const data = (event as MessageEvent).data;
  if (typeof data !== "string") return null;
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}
