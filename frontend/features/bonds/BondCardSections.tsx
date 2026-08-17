"use client";

import { CashflowChart } from "@/features/bonds/BondCharts";
import { ChangeHistoryPanel } from "@/features/charts/ChangeHistoryPanel";
import { SeriesPanel } from "@/features/charts/SeriesPanel";

/** Charts shown on every bond card, in both modes: the payment schedule, the
 *  price/yield history assembled from public data, and what actually changed. */
export function BondCharts({
  ticker,
  currency,
}: {
  ticker: string;
  currency: string;
}) {
  return (
    <div className="space-y-4">
      <CashflowChart ticker={ticker} currency={currency} />
      <SeriesPanel kind="bond" identifier={ticker} />
      <ChangeHistoryPanel kind="bond" identifier={ticker} />
    </div>
  );
}
