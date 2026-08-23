"use client";

import Link from "next/link";
import useSWR from "swr";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { bondsService } from "@/services/bonds";
import { stocksService } from "@/services/stocks";
import { formatRate } from "@/utils/format";

type Asset = "bonds" | "stocks" | "all";
type Profile = "conservative" | "balanced" | "growth" | "dividend";

export function HomeRecommendations({ asset, amount, profile }: { asset: Asset; amount: number; profile: Profile }) {
  const showStocks = asset === "stocks" || asset === "all";
  const showBonds = asset === "bonds" || asset === "all";
  const stocks = useSWR(showStocks && amount > 0 ? ["home-stock-recommendations", amount, profile] : null, () => stocksService.recommend(amount, profile));
  const bondProfile = profile === "conservative" ? "conservative" : profile === "growth" ? "aggressive" : "balanced";
  const bonds = useSWR(showBonds && amount > 0 ? ["home-bond-recommendations", amount, profile] : null, () => bondsService.recommend(amount, bondProfile));
  const loading = stocks.isLoading || bonds.isLoading;
  const failed = stocks.error || bonds.error;
  const empty = !stocks.data?.items.length && !bonds.data?.items.length;

  return <Card>
    <CardHeader title="Подбор под ваш бюджет" subtitle={`Сумма ${amount.toLocaleString("ru-RU")} ₸ · профиль: ${profile}`} />
    {loading ? <div className="space-y-2 p-4">{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-14 w-full" />)}</div>
      : failed ? <EmptyState title="Не удалось выполнить подбор" description="Проверьте доступность данных и повторите попытку." />
      : empty ? <EmptyState title="Подходящих инструментов пока нет" description="Измените сумму или профиль риска." />
      : <div className="divide-y divide-slate-100 dark:divide-slate-800">
        {stocks.data?.items.map((item) => <Link key={`stock-${item.id}`} href={`/stock/${item.ticker}`} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-800/60"><span className="rounded-md bg-emerald-100 px-2 py-1 text-[10px] font-bold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">АКЦИЯ</span><span className="min-w-0"><span className="block font-semibold">{item.ticker}</span><span className="block truncate text-xs text-slate-500">{item.company_name}</span></span><span className="text-sm font-semibold tabular">{item.scores.personal?.value == null ? "—" : `${Math.round(item.scores.personal.value)}/100`}</span></Link>)}
        {bonds.data?.items.map((item) => <Link key={`bond-${item.ticker}`} href={`/bond/${item.ticker}`} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-800/60"><span className="rounded-md bg-sky-100 px-2 py-1 text-[10px] font-bold text-sky-700 dark:bg-sky-950 dark:text-sky-300">ОБЛИГАЦИЯ</span><span className="min-w-0"><span className="block font-semibold">{item.ticker}</span><span className="block truncate text-xs text-slate-500">{item.issuer || "Эмитент не указан"}</span></span><span className="text-right text-xs"><span className="block font-semibold tabular">{item.investment_score == null ? "—" : `${Math.round(item.investment_score)}/100`}</span><span className="text-slate-400">YTM {formatRate(item.ytm_pct == null ? null : item.ytm_pct / 100)}</span></span></Link>)}
      </div>}
  </Card>;
}
