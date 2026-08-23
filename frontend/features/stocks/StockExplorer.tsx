"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { stocksService } from "@/services/stocks";
import { formatDate, formatMoney, formatRate } from "@/utils/format";

const SCORE_FILTERS = [
  ["quality", "Качество"], ["growth", "Рост"], ["valuation", "Valuation"],
  ["dividend", "Дивиденды"], ["liquidity", "Ликвидность"], ["risk", "Низкий риск"],
] as const;

export function StockExplorer() {
  const { data, isLoading, error } = useSWR("stocks-full-catalog", () => stocksService.list(500), { refreshInterval: 60_000 });
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("investment");
  const [scoreFilter, setScoreFilter] = useState("");
  const [minimum, setMinimum] = useState(0);
  const [sector, setSector] = useState("");
  const sectors = useMemo(() => [...new Set((data?.items ?? []).map(item => item.sector).filter(Boolean) as string[])].sort(), [data]);
  const items = useMemo(() => {
    const term = query.trim().toLocaleLowerCase("ru");
    return [...(data?.items ?? [])].filter(item => {
      const matchesText = !term || [item.ticker, item.isin, item.company_name, item.issuer].some(value => value?.toLocaleLowerCase("ru").includes(term));
      const matchesSector = !sector || item.sector === sector;
      const matchesScore = !scoreFilter || (item.scores[scoreFilter]?.value ?? -1) >= minimum;
      return matchesText && matchesSector && matchesScore;
    }).sort((a, b) => {
      if (sort === "price_change") return (b.change_percent ?? -Infinity) - (a.change_percent ?? -Infinity);
      if (sort === "dividend") return (b.metrics.trailing_dividend_yield ?? -Infinity) - (a.metrics.trailing_dividend_yield ?? -Infinity);
      return (b.scores[sort]?.value ?? -Infinity) - (a.scores[sort]?.value ?? -Infinity);
    });
  }, [data, minimum, query, scoreFilter, sector, sort]);

  return <Card><CardHeader title="Все доступные акции" subtitle={`${items.length} из ${data?.total ?? 0} · цена, изменение, источник и свежесть данных`}/>
    <CardBody className="grid gap-3 border-b border-slate-100 dark:border-slate-800 md:grid-cols-4">
      <label className="text-xs text-slate-500 md:col-span-2">Поиск по тикеру, ISIN, компании или эмитенту<input value={query} onChange={e => setQuery(e.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700" placeholder="HSBK или Kaspi"/></label>
      <label className="text-xs text-slate-500">Сортировка<select value={sort} onChange={e => setSort(e.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700"><option value="investment">Investment Score</option><option value="quality">Качество</option><option value="growth">Рост</option><option value="valuation">Valuation</option><option value="dividend">Дивиденды</option><option value="liquidity">Ликвидность</option><option value="risk">Низкий риск</option><option value="price_change">Изменение цены</option></select></label>
      <label className="text-xs text-slate-500">Отрасль<select value={sector} onChange={e => setSector(e.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700"><option value="">Все отрасли</option>{sectors.map(value => <option key={value}>{value}</option>)}</select></label>
      <div className="md:col-span-4"><div className="flex flex-wrap items-center gap-2"><span className="text-xs text-slate-500">Фильтр score:</span><button onClick={() => setScoreFilter("")} className={`rounded-lg px-2.5 py-1 text-xs ${!scoreFilter ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 dark:bg-slate-800"}`}>Все</button>{SCORE_FILTERS.map(([code,label]) => <button key={code} onClick={() => setScoreFilter(code)} className={`rounded-lg px-2.5 py-1 text-xs ${scoreFilter === code ? "bg-emerald-600 text-white" : "bg-slate-100 dark:bg-slate-800"}`}>{label}</button>)}{scoreFilter ? <label className="ml-auto flex items-center gap-2 text-xs text-slate-500">минимум <input type="range" min="0" max="100" step="5" value={minimum} onChange={e => setMinimum(Number(e.target.value))}/><strong>{minimum}</strong></label> : null}</div></div>
    </CardBody>
    {isLoading ? <CardBody className="space-y-2"><Skeleton className="h-16 w-full"/><Skeleton className="h-16 w-full"/></CardBody> : error ? <CardBody><EmptyState title="Каталог недоступен" description="Повторите запрос после следующей проверки KASE."/></CardBody> : !items.length ? <CardBody><EmptyState title="Ничего не найдено" description="Сбросьте фильтры или измените поисковый запрос."/></CardBody> : <div>{items.map(item => <Link key={item.id} href={`/stock/${item.ticker}`} className="grid gap-2 border-t border-slate-100 px-4 py-3 first:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50 sm:grid-cols-[1.4fr_repeat(4,minmax(0,.7fr))] sm:items-center"><span className="min-w-0"><strong className="block">{item.ticker}</strong><span className="block truncate text-xs text-slate-500">{item.company_name} · {item.isin || "ISIN —"}</span></span><Cell label="Цена" value={formatMoney(item.price,item.currency,2)}/><Cell label="Изменение" value={formatRate(item.change_percent)}/><Cell label="Score" value={item.scores.investment?.value == null ? "—" : `${Math.round(item.scores.investment.value)}/100`}/><Cell label="Источник" value={`${item.source ?? "—"} · ${formatDate(item.data_timestamp)}`}/></Link>)}</div>}
  </Card>;
}

function Cell({label,value}:{label:string;value:string}) { return <span><span className="block text-[10px] uppercase tracking-wide text-slate-400">{label}</span><span className="block truncate text-sm font-medium tabular">{value}</span></span>; }
