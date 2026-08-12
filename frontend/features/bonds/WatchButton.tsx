"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";

import { Button } from "@/components/ui/Button";
import { watchlistService } from "@/services/user";

export function WatchButton({ ticker }: { ticker: string }) {
  const { data } = useSWR("watchlist", () => watchlistService.list(), {
    revalidateOnFocus: false,
  });
  const [busy, setBusy] = useState(false);
  const saved = Boolean(data?.items.some((item) => item.ticker === ticker));

  async function toggle() {
    setBusy(true);
    try {
      if (saved) {
        await watchlistService.remove(ticker);
      } else {
        await watchlistService.add(ticker);
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
