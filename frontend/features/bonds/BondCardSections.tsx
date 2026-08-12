"use client";

import { CashflowChart, PriceHistoryChart } from "@/features/bonds/BondCharts";

/** Charts shown on every bond card, in both modes: a payment schedule and a
 *  price history are useful without any finance vocabulary. */
export function BondCharts({
  ticker,
  currency,
}: {
  ticker: string;
  currency: string;
}) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <CashflowChart ticker={ticker} currency={currency} />
      <PriceHistoryChart ticker={ticker} />
    </div>
  );
}
