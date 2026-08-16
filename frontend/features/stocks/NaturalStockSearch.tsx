"use client";

import Link from "next/link";
import { useState } from "react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { stocksService } from "@/services/stocks";
import type { StockListItem } from "@/types/api";
import { formatMoney } from "@/utils/format";

export function NaturalStockSearch() {
  const [query, setQuery] = useState("Найди недорогие акции KASE с дивидендами");
  const [items, setItems] = useState<StockListItem[]>([]);
  const [filters, setFilters] = useState<Record<string, string | number>>({});
  const [assumptions, setAssumptions] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function search() {
    try {
      setError(null);
      const result = await stocksService.interpretSearch(query);
      setItems(result.items); setFilters(result.validated_filters); setAssumptions(result.assumptions);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось применить фильтры");
    }
  }

  return <Card><CardHeader title="Поиск акций обычным языком" subtitle="AI-запрос преобразуется только в показанные проверяемые фильтры." /><CardBody className="space-y-3"><div className="flex flex-col gap-2 sm:flex-row"><input value={query} onChange={(event) => setQuery(event.target.value)} className="h-11 min-w-0 flex-1 rounded-xl border border-slate-200 bg-transparent px-3 dark:border-slate-700" /><button onClick={() => void search()} className="rounded-xl bg-emerald-600 px-5 text-sm font-semibold text-white">Найти акции</button></div>
    {error ? <p className="text-sm text-rose-600">{error}</p> : null}
    {Object.keys(filters).length ? <div className="flex flex-wrap gap-2">{Object.entries(filters).map(([key, value]) => <span key={key} className="rounded-lg bg-slate-100 px-2 py-1 text-xs dark:bg-slate-800">{key}: {String(value)}</span>)}</div> : null}
    {assumptions.map((item) => <p key={item} className="text-xs text-slate-500">{item}</p>)}
    {items.length ? <div className="divide-y divide-slate-100 dark:divide-slate-800">{items.map((item) => <Link key={item.id} href={`/stock/${item.ticker}`} className="flex items-center justify-between gap-3 py-2"><span><span className="block font-medium">{item.ticker}</span><span className="block text-xs text-slate-500">{item.company_name}</span></span><span className="text-right text-sm">{formatMoney(item.price, item.currency, 2)}<span className="block text-xs text-emerald-600">{item.scores.investment.value == null ? "нет оценки" : `${Math.round(item.scores.investment.value)}/100`}</span></span></Link>)}</div> : null}
  </CardBody></Card>;
}
