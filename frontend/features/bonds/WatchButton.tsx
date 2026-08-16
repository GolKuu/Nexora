"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";

import { Button } from "@/components/ui/Button";
import { watchlistService } from "@/services/user";

export function WatchButton({ ticker, instrumentType = "bond" }: { ticker: string; instrumentType?: "bond" | "stock" }) {
  const { data } = useSWR("watchlist", () => watchlistService.list(), {
    revalidateOnFocus: false,
  });
  const [busy, setBusy] = useState(false);
  const saved = Boolean(data?.items.some((item) => item.ticker === ticker &&
    (instrumentType === "bond" ? item.instrument_type === "bond" : item.instrument_type !== "bond")));

  async function toggle() {
    setBusy(true);
    try {
      if (saved) {
        await watchlistService.remove(ticker, instrumentType);
      } else {
        await watchlistService.add(ticker, instrumentType);
      }
      await mutate("watchlist");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button variant="secondary" size="sm" onClick={() => void toggle()} disabled={busy}>
      {saved ? "★ В избранном" : "☆ В избранное"}
    </Button>
  );
}
