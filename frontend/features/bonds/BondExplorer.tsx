"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { bondsService } from "@/services/bonds";
import { formatDate, formatMoney, formatPercent } from "@/utils/format";

const TYPES = [["", "Все"], ["government", "Государственные"], ["quasi_sovereign", "Квазигосударственные"], ["bank", "Банковские"], ["corporate", "Корпоративные"]] as const;

export function BondExplorer() {
  const { data, isLoading, error, mutate } = useSWR("bonds-full-catalog", () => bondsService.list({ limit: 200 }), { refreshInterval: 60_000 });
  const [query, setQuery] = useState(""); const [type, setType] = useState(""); const [currency, setCurrency] = useState("");
  const [maxYears, setMaxYears] = useState(0); const [sort, setSort] = useState("investment_score");
  const items = useMemo(() => {
    const term = query.trim().toLocaleLowerCase("ru");
    return [...(data?.items ?? [])].filter(item => (!term || [item.ticker,item.isin,item.name,item.issuer_name].some(v => v?.toLocaleLowerCase("ru").includes(term))) && (!type || item.bond_type === type) && (!currency || item.currency === currency) && (!maxYears || (item.years_to_maturity ?? Infinity) <= maxYears)).sort((a,b) => ((b[sort as keyof typeof b] as number | null) ?? -Infinity) - ((a[sort as keyof typeof a] as number | null) ?? -Infinity));
  }, [currency,data,maxYears,query,sort,type]);
  // Four explicit states. The order matters: an error must win over "loading",
  // and "no data yet" must never fall through to the count branch - with
  // `data` undefined that branch rendered a confident "0 из 0", which is the
  // one thing an empty-looking catalogue must never say when nothing was
  // actually loaded.
  const catalogStatus = error
    ? "Не удалось загрузить список облигаций"
    : !data
      ? "Загрузка каталога…"
      : `${items.length} из ${data.total} · YTM, купон, погашение, кредит и ликвидность`;
  return <Card><CardHeader title="Все доступные облигации" subtitle={catalogStatus}/>
    <CardBody className="grid gap-3 border-b border-slate-100 dark:border-slate-800 sm:grid-cols-2 lg:grid-cols-5">
      <label className="text-xs text-slate-500 lg:col-span-2">Тикер, ISIN, название или эмитент<input value={query} onChange={e=>setQuery(e.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700" placeholder="KZ2C... или Halyk"/></label>
      <label className="text-xs text-slate-500">Тип<select value={type} onChange={e=>setType(e.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700">{TYPES.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
      <label className="text-xs text-slate-500">Валюта<select value={currency} onChange={e=>setCurrency(e.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700"><option value="">Все</option><option>KZT</option><option>USD</option><option>EUR</option><option>RUB</option></select></label>
      <label className="text-xs text-slate-500">Сортировка<select value={sort} onChange={e=>setSort(e.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700"><option value="investment_score">Investment Score</option><option value="yield_pct">YTM</option><option value="credit_score">Надёжность</option><option value="liquidity_score">Ликвидность</option><option value="real_yield_pct">Реальная доходность</option><option value="hold_score">Hold Score</option><option value="trade_score">Trade Score</option></select></label>
      <label className="flex items-center gap-3 text-xs text-slate-500 sm:col-span-2 lg:col-span-5">Погашение: {maxYears ? `до ${maxYears} лет` : "любой срок"}<input className="flex-1" type="range" min="0" max="20" step="1" value={maxYears} onChange={e=>setMaxYears(Number(e.target.value))}/></label>
    </CardBody>
    {!data && !error ? <CardBody className="space-y-2"><Skeleton className="h-16 w-full"/><Skeleton className="h-16 w-full"/></CardBody> : error ? <CardBody><EmptyState title="Не удалось загрузить список облигаций" description="Проверьте соединение и повторите запрос. Ошибка не считается пустым каталогом." action={<button type="button" onClick={() => void mutate()} className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-slate-900">Повторить</button>}/></CardBody> : !items.length ? <CardBody><EmptyState title={data?.total === 0 ? "Инструменты пока не найдены" : "Ничего не найдено"} description={data?.total === 0 ? "Каталог KASE успешно загружен, но в нём пока нет доступных облигаций." : "Сбросьте фильтры или измените запрос."}/></CardBody> : <div>{items.map(item=><Link key={item.id} href={`/bond/${item.ticker}`} className="grid gap-2 border-t border-slate-100 px-4 py-3 first:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50 sm:grid-cols-[1.5fr_repeat(5,minmax(0,.7fr))] sm:items-center"><span className="min-w-0"><strong className="block">{item.ticker}</strong><span className="block truncate text-xs text-slate-500">{item.issuer_name ?? item.name} · {item.isin ?? "ISIN —"}</span></span><Cell label="YTM" value={formatPercent(item.yield_pct)}/><Cell label="Купон" value={formatPercent(item.coupon_rate_pct)}/><Cell label="Погашение" value={formatDate(item.maturity_date)}/><Cell label="Credit" value={score(item.credit_score)}/><Cell label="Score / цена" value={`${score(item.investment_score)} · ${formatMoney(item.clean_price,item.currency,2)}`}/></Link>)}</div>}
  </Card>;
}
function score(value:number|null|undefined){return value == null ? "—" : `${Math.round(value)}/100`;}
function Cell({label,value}:{label:string;value:string}){return <span><span className="block text-[10px] uppercase tracking-wide text-slate-400">{label}</span><span className="block truncate text-sm font-medium tabular">{value}</span></span>;}
