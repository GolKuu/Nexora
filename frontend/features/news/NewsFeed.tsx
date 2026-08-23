"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { marketService } from "@/services/market";
import { useSettings } from "@/hooks/useSettings";
import { cn } from "@/utils/cn";
import { formatDate } from "@/utils/format";

const FILTERS = [
  ["", "Все"], ["earnings", "Отчётность"], ["dividend", "Дивиденды"],
  ["rating_change", "Рейтинги"], ["regulation", "Регулирование"],
  ["interest_rate", "Ставки"], ["oil", "Нефть"],
] as const;

const LABELS: Record<string, string> = {
  earnings: "Отчётность", dividend: "Дивиденд", product_launch: "Продукт",
  new_contract: "Контракт", "M&A": "M&A", management_change: "Менеджмент",
  rating_change: "Рейтинг", regulation: "Регулирование", lawsuit: "Суд",
  capital_raise: "Капитал", share_issuance: "Выпуск акций", buyback: "Buyback",
  debt_event: "Долг", restructuring: "Реструктуризация", default: "Дефолт",
  macro_event: "Макро", interest_rate: "Ставка", inflation: "Инфляция",
  FX: "Валюта", oil: "Нефть", sector_event: "Отрасль",
};

export function NewsFeed({ compact = false }: { compact?: boolean }) {
  const [eventType, setEventType] = useState("");
  const { settings } = useSettings();
  const { data, isLoading, error } = useSWR(
    ["market-news", eventType, compact],
    () => marketService.news({ limit: compact ? 5 : 80, eventType: eventType || undefined }),
    { refreshInterval: 60_000, revalidateOnFocus: true },
  );
  const items = (data?.items ?? []).filter((item) => {
    const kase = item.source.toLocaleLowerCase("ru").includes("kase");
    return kase ? settings?.kase_news_enabled !== false : settings?.external_news_enabled !== false;
  });

  if (settings?.news_enabled === false) {
    return <Card><CardHeader title="Новости отключены" subtitle="Включить ленту можно в настройках." /></Card>;
  }

  return <Card>
    <CardHeader title={compact ? "Важные новости" : "Новости и рыночные события"}
      subtitle="Дедуплицированные публикации KASE, эмитентов и разрешённых источников с фактической реакцией рынка." />
    {!compact ? <div className="flex gap-1.5 overflow-x-auto border-b border-slate-100 px-4 py-2.5 dark:border-slate-800">
      {FILTERS.map(([code, label]) => <button key={code} onClick={() => setEventType(code)}
        className={cn("shrink-0 rounded-lg px-3 py-1.5 text-xs font-medium", eventType === code ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300")}>{label}</button>)}
    </div> : null}
    {isLoading ? <CardBody className="space-y-2"><Skeleton className="h-20 w-full"/><Skeleton className="h-20 w-full"/></CardBody>
      : error ? <CardBody><EmptyState title="Новости временно недоступны" description="Сайт продолжает работать с последними сохранёнными рыночными данными."/></CardBody>
      : !items.length ? <CardBody><EmptyState title="Новых событий пока нет" description="Лента заполнится после следующего цикла сбора новостей или после включения источников в настройках."/></CardBody>
      : <div>{items.map(item => <article key={item.id} className="border-t border-slate-100 px-4 py-4 first:border-0 dark:border-slate-800">
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <Badge>{item.marker}</Badge><span>{LABELS[item.event_type] ?? item.event_type}</span>
          {item.ticker ? <Link href={`/stock/${item.ticker}`} className="font-semibold text-emerald-700 hover:underline dark:text-emerald-400">{item.ticker}</Link> : null}
          <span>· {item.source} · {formatDate(item.published_at)}</span>
        </div>
        <a href={item.source_url} target="_blank" rel="noreferrer" className="mt-1.5 block font-semibold leading-snug hover:underline">{item.title} ↗</a>
        {!compact && item.summary ? <p className="mt-1 text-sm text-slate-500">{item.summary}</p> : null}
        {!compact ? <div className="mt-2 grid gap-2 text-xs text-slate-500 sm:grid-cols-2">
          <p>{item.explanation}</p><p>Важность {Math.round(item.importance * 100)}% · уверенность источника {Math.round(item.source_confidence * 100)}%</p>
        </div> : null}
      </article>)}</div>}
    {compact ? <CardBody className="border-t border-slate-100 dark:border-slate-800"><Link href="/news" className="text-sm font-semibold text-emerald-700 hover:underline dark:text-emerald-400">Все новости →</Link></CardBody> : null}
  </Card>;
}
