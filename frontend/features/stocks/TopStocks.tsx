"use client";

import Link from "next/link";
import { useState } from "react";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { useTopStocks } from "@/hooks/useStocks";
import { formatMoney, formatRate } from "@/utils/format";
import { cn } from "@/utils/cn";

const CATEGORIES = [["best", "Лучшие"], ["quality", "Качество"], ["undervalued", "Оценка"], ["growth", "Рост"], ["dividends", "Дивиденды"], ["liquid", "Ликвидность"], ["low_risk", "Низкий риск"]] as const;

export function TopStocks({ limit = 12 }: { limit?: number }) {
  const [category, setCategory] = useState("best");
  const { data, isLoading, error } = useTopStocks(category, limit);
  return <Card><CardHeader title="Акции KASE" subtitle="Отдельная equity-модель: качество, оценка, дивиденды, ликвидность и риск." />
    <div className="flex gap-1.5 overflow-x-auto border-b border-slate-100 px-4 py-2.5 dark:border-slate-800">{CATEGORIES.map(([code, label]) => <button key={code} onClick={() => setCategory(code)} className={cn("shrink-0 rounded-lg px-3 py-1 text-xs font-medium", category === code ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300")}>{label}</button>)}</div>
    {isLoading ? <div className="space-y-2 p-4">{Array.from({length: 5}).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}</div> : error ? <EmptyState title="Не удалось загрузить акции" description="Обновите официальный каталог KASE через backend." /> : !data?.items.length ? <EmptyState title="Каталог акций пока пуст" description="Запустите POST /stocks/refresh для загрузки реальных данных KASE." /> : <div>{data.items.map((stock, index) => <Link key={stock.id} href={`/stock/${stock.ticker}`} className="grid grid-cols-[2rem_1fr_auto] items-center gap-3 border-b border-slate-100 px-4 py-3 last:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"><span className="text-xs text-slate-400">{index + 1}</span><span className="min-w-0"><span className="block font-semibold">{stock.ticker}</span><span className="block truncate text-xs text-slate-500">{stock.company_name} · {stock.type_label}</span></span><span className="text-right"><span className="block tabular font-semibold">{formatMoney(stock.price, stock.currency, 2)}</span><span className="block text-xs text-emerald-600">{stock.scores.investment?.value == null ? "нет оценки" : `${Math.round(stock.scores.investment.value)}/100`} · див. {formatRate(stock.metrics.trailing_dividend_yield)}</span></span></Link>)}</div>}
  </Card>;
}
