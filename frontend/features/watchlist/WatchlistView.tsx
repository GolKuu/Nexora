"use client";

import useSWR from "swr";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { BondRow } from "@/features/bonds/BondRow";
import { watchlistService } from "@/services/user";
import Link from "next/link";
import { useState } from "react";
import { formatMoney, formatRate } from "@/utils/format";
import { useUiStore } from "@/stores/uiStore";

export function WatchlistView() {
  const { data, isLoading, mutate } = useSWR("watchlist", () => watchlistService.list(), {
    revalidateOnFocus: false,
  });
  const [removing, setRemoving] = useState<string | null>(null);
  const compareList = useUiStore((state) => state.compareList);
  const toggleCompare = useUiStore((state) => state.toggleCompare);

  async function remove(identifier: string, instrumentType: "bond" | "stock") {
    const key = `${instrumentType}-${identifier}`;
    setRemoving(key);
    try {
      await watchlistService.remove(identifier, instrumentType);
      await mutate();
    } finally {
      setRemoving(null);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Избранное"
        subtitle="Сохраняется в этом браузере. Регистрация нужна только для синхронизации между устройствами."
      />
      {isLoading ? (
        <CardBody>
          <Skeleton className="h-24 w-full" />
        </CardBody>
      ) : !data?.items.length ? (
        <CardBody>
          <EmptyState
            title="Список пуст"
            description="Откройте карточку облигации или акции и нажмите «В избранное»."
          />
        </CardBody>
      ) : (
        <div>
          {data.items.map((item) => item.instrument_type === "bond" ? (
            <div key={`bond-${item.id}`} className="relative border-b border-slate-100 pr-16 last:border-0 dark:border-slate-800"><BondRow bond={item} /><button type="button" onClick={() => void remove(item.ticker, "bond")} disabled={removing === `bond-${item.ticker}`} className="absolute right-3 top-1/2 -translate-y-1/2 rounded-lg px-2 py-1 text-xs text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:hover:bg-rose-950">{removing === `bond-${item.ticker}` ? "…" : "Удалить"}</button></div>
          ) : (
            <div key={`stock-${item.id}`} className="grid gap-3 border-b border-slate-100 px-4 py-3 last:border-0 dark:border-slate-800 sm:grid-cols-[1fr_auto_auto] sm:items-center"><Link href={`/stock/${item.ticker}`} className="contents hover:bg-slate-50 dark:hover:bg-slate-800/50">
              <span className="min-w-0"><span className="block font-semibold">{item.ticker}</span><span className="block truncate text-xs text-slate-500">{item.company_name} · {item.type_label}</span></span>
              <span className="text-left sm:text-right"><span className="block font-semibold tabular">{formatMoney(item.price, item.currency, 2)}</span><span className="block text-xs text-emerald-600">{item.scores.investment?.value == null ? "нет оценки" : `${Math.round(item.scores.investment.value)}/100`}{item.change_percent == null ? "" : ` · ${item.change_percent >= 0 ? "+" : ""}${formatRate(item.change_percent)}`}</span><span className="mt-1 block text-xs text-sky-600">DCF: {item.dcf_summary?.base_fair_value == null ? (item.dcf_summary?.status === "stale" ? "устарел" : "не рассчитан") : `${formatMoney(item.dcf_summary.base_fair_value, item.currency, 0)}${item.dcf_summary.base_difference_percent == null ? "" : ` · ${item.dcf_summary.base_difference_percent > 0 ? "+" : ""}${item.dcf_summary.base_difference_percent.toFixed(1)}%`}`}</span></span></Link><span className="flex flex-wrap justify-end gap-1"><Link href={`/stock/${item.ticker}#dcf`} className="rounded-lg px-2 py-1 text-xs text-sky-600 hover:bg-sky-50 dark:hover:bg-sky-950">Анализ</Link><button type="button" onClick={() => toggleCompare(item.ticker)} className="rounded-lg px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">{compareList.includes(item.ticker) ? "✓ Сравнить" : "+ Сравнить"}</button><button type="button" onClick={() => void remove(item.ticker, "stock")} disabled={removing === `stock-${item.ticker}`} className="rounded-lg px-2 py-1 text-xs text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:hover:bg-rose-950">{removing === `stock-${item.ticker}` ? "…" : "Удалить"}</button></span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
