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
  const [limit, setLimit] = useState(40);
  const { data, isLoading, error, mutate } = useSWR(["stocks-catalog", limit], () => stocksService.list(limit), { refreshInterval: 60_000, keepPreviousData: true });
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

  // Four explicit states. The order matters: an error must win over "loading",
  // and "no data yet" must never fall through to the count branch - with
  // `data` undefined that branch rendered a confident "0 из 0", which is the
  // one thing an empty-looking catalogue must never say when nothing was
  // actually loaded.
  const catalogStatus = error
    ? "Не удалось загрузить список акций"
    : !data
      ? "Загрузка каталога…"
      : `${items.length} из ${data.total} · цена, изменение, источник и свежесть данных`;

  return <Card><CardHeader title="Все доступные акции" subtitle={catalogStatus}/>
    <CardBody className="grid gap-3 border-b border-slate-100 dark:border-slate-800 md:grid-cols-4">
      <label className="text-xs text-slate-500 md:col-span-2">Поиск по тикеру, ISIN, компании или эмитенту<input value={query} onChange={e => setQuery(e.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700" placeholder="HSBK или Kaspi"/></label>
      <label className="text-xs text-slate-500">Сортировка<select value={sort} onChange={e => setSort(e.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700"><option value="investment">Investment Score</option><option value="quality">Качество</option><option value="growth">Рост</option><option value="valuation">Valuation</option><option value="dividend">Дивиденды</option><option value="liquidity">Ликвидность</option><option value="risk">Низкий риск</option><option value="price_change">Изменение цены</option></select></label>
      <label className="text-xs text-slate-500">Отрасль<select value={sector} onChange={e => setSector(e.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700"><option value="">Все отрасли</option>{sectors.map(value => <option key={value}>{value}</option>)}</select></label>
      <div className="md:col-span-4"><div className="flex flex-wrap items-center gap-2"><span className="text-xs text-slate-500">Фильтр score:</span><button onClick={() => setScoreFilter("")} className={`rounded-lg px-2.5 py-1 text-xs ${!scoreFilter ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 dark:bg-slate-800"}`}>Все</button>{SCORE_FILTERS.map(([code,label]) => <button key={code} onClick={() => setScoreFilter(code)} className={`rounded-lg px-2.5 py-1 text-xs ${scoreFilter === code ? "bg-emerald-600 text-white" : "bg-slate-100 dark:bg-slate-800"}`}>{label}</button>)}{scoreFilter ? <label className="ml-auto flex items-center gap-2 text-xs text-slate-500">минимум <input type="range" min="0" max="100" step="5" value={minimum} onChange={e => setMinimum(Number(e.target.value))}/><strong>{minimum}</strong></label> : null}</div></div>
    </CardBody>
    {!data && !error ? <CardBody className="space-y-2"><Skeleton className="h-16 w-full"/><Skeleton className="h-16 w-full"/></CardBody> : error ? <CardBody><EmptyState title="Не удалось загрузить список акций" description="Проверьте соединение и повторите запрос. Ошибка не считается пустым каталогом." action={<button type="button" onClick={() => void mutate()} className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-slate-900">Повторить</button>}/></CardBody> : !items.length ? <CardBody><EmptyState title={data?.total === 0 ? "Инструменты пока не найдены" : "Ничего не найдено"} description={data?.total === 0 ? "Каталог KASE успешно загружен, но в нём пока нет доступных акций." : "Сбросьте фильтры или измените поисковый запрос."}/></CardBody> : <div>{items.map(item => <Link key={item.id} href={`/stock/${item.ticker}`} className="grid gap-2 border-t border-slate-100 px-4 py-3 first:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50 sm:grid-cols-[1.3fr_repeat(5,minmax(0,.65fr))] sm:items-center"><span className="min-w-0"><strong className="block">{item.ticker}</strong><span className="block truncate text-xs text-slate-500">{item.company_name} · {item.isin || "ISIN —"}</span></span><Cell label="Цена" value={formatMoney(item.price,item.currency,2)}/><Cell label="Изменение" value={formatRate(item.change_percent)}/><Cell label="DCF Base" value={item.dcf_summary?.base_fair_value == null ? dcfStatusLabel(item.dcf_summary?.status) : formatMoney(item.dcf_summary.base_fair_value,item.currency,0)}/><Cell label="vs рынок" value={item.dcf_summary?.base_difference_percent == null ? "—" : `${item.dcf_summary.base_difference_percent > 0 ? "+" : ""}${item.dcf_summary.base_difference_percent.toFixed(1)}%`}/><Cell label="Score" value={item.scores.investment?.value == null ? "—" : `${Math.round(item.scores.investment.value)}/100`}/><Cell label="Источник" value={`${item.source ?? "—"} · ${formatDate(item.data_timestamp)}`}/></Link>)}{(data?.items.length ?? 0) < (data?.total ?? 0) ? <div className="border-t border-slate-100 p-4 text-center dark:border-slate-800"><button type="button" disabled={isLoading} onClick={() => setLimit(value => Math.min(value + 40, 500))} className="rounded-xl border border-slate-200 px-5 py-2 text-sm font-medium dark:border-slate-700">{isLoading ? "Загрузка…" : "Показать ещё"}</button></div> : null}</div>}
  </Card>;
}

function Cell({label,value}:{label:string;value:string}) { return <span><span className="block text-[10px] uppercase tracking-wide text-slate-400">{label}</span><span className="block truncate text-sm font-medium tabular">{value}</span></span>; }
function dcfStatusLabel(status?: string) { return status === "stale" ? "Устарел" : status === "available" ? "Доступен" : "Не рассчитан"; }
