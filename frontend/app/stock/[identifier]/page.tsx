"use client";

import Link from "next/link";
import { use } from "react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ScoreBar, ScoreDial } from "@/components/ui/ScoreDial";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { WatchButton } from "@/features/bonds/WatchButton";
import { ChangeHistoryPanel } from "@/features/charts/ChangeHistoryPanel";
import { SeriesPanel } from "@/features/charts/SeriesPanel";
import { NewsImpactPanel } from "@/features/stocks/NewsImpactPanel";
import { ForecastPanel } from "@/features/stocks/ForecastPanel";
import { StockAlerts } from "@/features/stocks/StockAlerts";
import { StockCalculator } from "@/features/stocks/StockCalculator";
import { useStockCard } from "@/hooks/useStocks";
import { useUiStore } from "@/stores/uiStore";
import { formatCompact, formatDate, formatMoney, formatNumber, formatRate } from "@/utils/format";

const SCORE_LABELS:Record<string,string>={quality:"Качество",valuation:"Оценка",growth:"Рост",dividend:"Дивиденды",liquidity:"Ликвидность",risk:"Риск"};

export default function StockPage({params}:{params:Promise<{identifier:string}>}) {
  const {identifier}=use(params); const {data,isLoading,error}=useStockCard(decodeURIComponent(identifier)); const uiMode=useUiStore(s=>s.uiMode);
  if(isLoading)return <div className="space-y-4"><Skeleton className="h-10 w-64"/><Skeleton className="h-64 w-full"/></div>;
  if(error||!data)return <Card><CardBody><EmptyState title="Акция не найдена" description="Проверьте тикер или ISIN." action={<Link href="/stocks" className="underline">К акциям</Link>}/></CardBody></Card>;
  return <div className="space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><h1 className="text-3xl font-semibold">{data.ticker}</h1><Badge tone="success">{data.type_label}</Badge></div><p className="mt-1 text-sm text-slate-500">{data.company_name} · данные {formatDate(data.data_timestamp)}</p></div><div className="flex items-center gap-2"><WatchButton ticker={data.ticker} instrumentType="stock"/><a href={data.kase_url??"#"} target="_blank" rel="noreferrer" className="rounded-xl border border-slate-200 px-3 py-2 text-sm dark:border-slate-700">Открыть на KASE ↗</a></div></div>
    <ForecastPanel ticker={data.ticker} currency={data.currency}/>
    <div className="grid gap-4 lg:grid-cols-3"><div className="space-y-4 lg:col-span-2">
      <Card><CardBody className="grid gap-5 sm:grid-cols-[auto_1fr]"><ScoreDial value={data.scores.investment?.value} label="из 100" caption="Общая оценка"/><div><p className="text-3xl font-semibold tabular">{formatMoney(data.price,data.currency,2)}</p><p className="mt-1 text-sm text-slate-500">{data.simple.valuation}. {data.simple.important}</p><div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">{Object.entries(SCORE_LABELS).map(([kind,label])=><ScoreBar key={kind} label={label} value={data.scores[kind]?.value}/>)}</div></div></CardBody></Card>
      <SeriesPanel kind="stock" identifier={data.ticker}/>
      <ChangeHistoryPanel kind="stock" identifier={data.ticker}/>
      <NewsImpactPanel ticker={data.ticker}/>
      <Card><CardHeader title="Почему такая оценка?" subtitle="Недоступные показатели не получают ноль — они снижают Data Quality."/><CardBody className="grid gap-3 sm:grid-cols-2">{data.score_explanation.filter(s=>SCORE_LABELS[s.kind]).map(score=><div key={score.kind} className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800"><div className="flex justify-between"><span>{SCORE_LABELS[score.kind]}</span><strong>{score.value==null?"нет данных":`${Math.round(score.value)}/100`}</strong></div><p className="mt-1 text-xs text-slate-500">Покрытие данных: {Math.round(score.confidence*100)}%</p></div>)}</CardBody></Card>
      {uiMode==="pro"?<Card><CardHeader title="Pro показатели" subtitle="Для банков EV/EBITDA не используется как основной показатель."/><CardBody className="grid grid-cols-2 gap-4 sm:grid-cols-3"><Metric label="P/E" value={formatNumber(data.metrics.pe)}/><Metric label="P/B" value={formatNumber(data.metrics.pb)}/><Metric label="EV/EBITDA" value={formatNumber(data.metrics.ev_ebitda)}/><Metric label="Dividend Yield" value={formatRate(data.metrics.trailing_dividend_yield)}/><Metric label="ROE" value={formatRate(data.metrics.roe)}/><Metric label="ROA" value={formatRate(data.metrics.roa)}/><Metric label="Revenue Growth" value={formatRate(data.metrics.revenue_growth)}/><Metric label="Profit Growth" value={formatRate(data.metrics.earnings_growth)}/><Metric label="Net margin" value={formatRate(data.metrics.net_margin)}/><Metric label="Net debt" value={formatCompact(data.metrics.net_debt)}/><Metric label="Bid / Ask" value={`${formatMoney(data.bid,data.currency,2)} / ${formatMoney(data.ask,data.currency,2)}`}/></CardBody></Card>:null}
      {data.dividends.length?<Card><CardHeader title="Дивиденды" subtitle="Только подтверждённые публикации KASE; неизвестные даты не прогнозируются."/><CardBody className="space-y-3">{data.dividends.slice(0,5).map((dividend,index)=><div key={dividend.source_url??index} className="flex items-center justify-between gap-3"><span><span className="block text-sm font-medium">{formatMoney(dividend.dividend_per_share,dividend.currency,2)} на акцию</span><span className="block text-xs text-slate-500">{dividend.status} · {formatDate(dividend.payment_date??dividend.record_date)}</span></span>{dividend.source_url?<a href={dividend.source_url} target="_blank" rel="noreferrer" className="text-xs underline">KASE ↗</a>:null}</div>)}</CardBody></Card>:null}
    </div><div className="space-y-4"><StockCalculator ticker={data.ticker} currency={data.currency}/><StockAlerts ticker={data.ticker}/><Card><CardHeader title="Источник и свежесть"/><CardBody className="space-y-2 text-sm"><p><span className="text-slate-500">Источник:</span> {data.source??"—"}</p><p><span className="text-slate-500">На дату:</span> {formatDate(data.data_timestamp)}</p><p className="text-xs text-slate-500">Last не гарантирует цену покупки. Калькулятор предпочитает актуальный ask и предупреждает при fallback.</p></CardBody></Card></div></div>
  </div>;
}

function Metric({label,value}:{label:string;value:string}){return <div><p className="text-xs text-slate-500">{label}</p><p className="mt-1 tabular font-semibold">{value}</p></div>}
