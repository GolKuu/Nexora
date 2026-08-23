"use client";

import Link from "next/link";
import useSWR from "swr";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { NewsFeed } from "@/features/news/NewsFeed";
import { stocksService } from "@/services/stocks";
import { portfolioService, watchlistService } from "@/services/user";
import { formatDate, formatMoney, formatRate } from "@/utils/format";

const LINKS = [
  ["/compare", "Сравнить", "Акции и облигации при одинаковой сумме"],
  ["/watchlist", "Избранное", "Цены, score, новости и red flags"],
  ["/portfolio", "Портфель", "P/L, доход, концентрации и история"],
  ["/system", "Состояние данных", "KASE, мониторинг, источники и свежесть"],
] as const;

export function HomePulse() {
  const { data: watchlist } = useSWR("home-watchlist", watchlistService.list, { revalidateOnFocus: false });
  const { data: portfolios } = useSWR("home-portfolios", portfolioService.list, { revalidateOnFocus: false });
  const firstPortfolio = portfolios?.items[0];
  const { data: portfolio } = useSWR(firstPortfolio ? ["home-portfolio", firstPortfolio.id] : null, () => portfolioService.detail(firstPortfolio!.id), { revalidateOnFocus: false });
  const { data: stocks } = useSWR("home-market-pulse", () => stocksService.list(100), { refreshInterval: 60_000 });
  const recent = [...(stocks?.items ?? [])].filter(item => item.data_timestamp).sort((a,b) => new Date(b.data_timestamp!).getTime() - new Date(a.data_timestamp!).getTime()).slice(0,5);
  const movers = [...(stocks?.items ?? [])].filter(item => item.change_percent != null).sort((a,b) => Math.abs(b.change_percent!) - Math.abs(a.change_percent!)).slice(0,5);
  return <div className="grid gap-5 lg:grid-cols-2">
    <NewsFeed compact />
    <div className="space-y-5">
      <Card><CardHeader title="Ваш обзор" subtitle="Краткое состояние локального профиля без регистрации."/><CardBody className="grid grid-cols-2 gap-3">
        <Link href="/watchlist" className="rounded-2xl bg-slate-50 p-4 dark:bg-slate-800"><span className="block text-2xl font-semibold">{watchlist?.items.length ?? 0}</span><span className="text-xs text-slate-500">в избранном</span></Link>
        <Link href="/portfolio" className="rounded-2xl bg-slate-50 p-4 dark:bg-slate-800"><span className="block text-2xl font-semibold">{portfolio ? formatMoney(portfolio.summary.market_value, portfolio.base_currency) : (portfolios?.items.reduce((sum, item) => sum + item.position_count, 0) ?? 0)}</span><span className="text-xs text-slate-500">{portfolio ? `стоимость · P/L ${formatMoney(portfolio.summary.unrealized_pnl, portfolio.base_currency)}` : "позиций в портфелях"}</span></Link>
      </CardBody></Card>
      <Card><CardHeader title="Быстрые переходы"/><CardBody className="grid gap-2 sm:grid-cols-2">{LINKS.map(([href,title,description]) => <Link key={href} href={href} className="rounded-xl border border-slate-200 p-3 hover:border-emerald-400 dark:border-slate-700"><span className="block text-sm font-semibold">{title} →</span><span className="mt-1 block text-xs text-slate-500">{description}</span></Link>)}</CardBody></Card>
    </div>
    <Card><CardHeader title="Недавно обновлены" subtitle="Последние сохранённые котировки; дата отражает фактическое покрытие."/><div>{recent.length ? recent.map(item => <Link key={item.id} href={`/stock/${item.ticker}`} className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-sm first:border-0 dark:border-slate-800"><span><strong>{item.ticker}</strong><span className="ml-2 text-xs text-slate-500">{item.company_name}</span></span><span className="text-xs text-slate-500">{formatDate(item.data_timestamp)}</span></Link>) : <CardBody><p className="text-sm text-slate-500">Свежие котировки появятся после обновления каталога.</p></CardBody>}</div></Card>
    <Card><CardHeader title="Что изменилось на рынке?" subtitle="Крупнейшие подтверждённые изменения последней доступной торговой сессии."/><div>{movers.length ? movers.map(item => <Link key={item.id} href={`/stock/${item.ticker}`} className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-sm first:border-0 dark:border-slate-800"><span><strong>{item.ticker}</strong><span className="ml-2 text-xs text-slate-500">{formatMoney(item.price,item.currency,2)}</span></span><span className={item.change_percent! >= 0 ? "font-semibold text-emerald-600" : "font-semibold text-rose-600"}>{formatRate(item.change_percent)}</span></Link>) : <CardBody><p className="text-sm text-slate-500">Значимых подтверждённых изменений пока нет.</p></CardBody>}</div></Card>
  </div>;
}
