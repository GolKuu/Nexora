"use client";

import { useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { stocksService } from "@/services/stocks";
import { useSettings } from "@/hooks/useSettings";
import { ApiError } from "@/services/client";
import type { DCFFinancialChanges2Y, DCFFinancialPeriod, DCFResult, DCFScenarioValue } from "@/types/api";
import { formatCompact, formatDate, formatMoney, formatRate } from "@/utils/format";

const SCENARIOS: Array<{ key: "bear" | "base" | "bull"; label: string; tone: string }> = [
  { key: "bear", label: "Негативный", tone: "text-rose-600 dark:text-rose-400" },
  { key: "base", label: "Базовый", tone: "text-sky-600 dark:text-sky-400" },
  { key: "bull", label: "Оптимистичный", tone: "text-emerald-600 dark:text-emerald-400" },
];

const DCF_INPUT_LABELS: Record<string, string> = {
  latest_financial_report: "последний годовой финансовый отчёт",
  revenue: "выручка",
  operating_profit: "операционная прибыль",
  cash_and_debt: "денежные средства и долг",
  shares_outstanding: "число акций в обращении",
  market_price: "рыночная цена",
  macro_assumptions: "безрисковая ставка и инфляция",
  capex: "капитальные затраты",
};

export function DCFValuationPanel({ ticker, currency, currentPrice }: { ticker: string; currency: string; currentPrice: number | null }) {
  const [result, setResult] = useState<DCFResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { settings } = useSettings();
  const latest = useSWR(["dcf-latest", ticker], () => stocksService.latestDcf(ticker), {
    revalidateOnFocus: false,
    dedupingInterval: 60_000,
  });
  const displayedResult = result ?? latest.data?.result ?? null;

  async function analyze() {
    setLoading(true); setError(null);
    try {
      const calculated = await stocksService.analyzeDcf(ticker);
      setResult(calculated);
      await latest.mutate({ available: true, result: calculated, usage: calculated.usage }, false);
    }
    catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.details.methodology === "unsupported_financial_institution") {
          setError("DCF не применяется к банкам и другим финансовым организациям. Для них нужна отдельная модель собственного капитала.");
        } else if (Array.isArray(caught.details.missing)) {
          const missing = caught.details.missing.map((key) => DCF_INPUT_LABELS[String(key)] ?? String(key));
          setError(`DCF пока недоступен: в опубликованных данных не хватает: ${missing.join(", ")}. Значения не подставляются искусственно.`);
        } else {
          setError(caught.message);
        }
      } else setError("Не удалось выполнить оценку. Попробуйте позже.");
    } finally { setLoading(false); }
  }

  return <section id="dcf"><Card className="overflow-hidden border-sky-200 bg-gradient-to-br from-white via-white to-sky-50 dark:border-sky-900 dark:from-slate-900 dark:via-slate-900 dark:to-sky-950/40">
    <CardHeader title="AI DCF Valuation" subtitle="Детерминированная оценка справедливой стоимости по финансовой отчётности — не торговая рекомендация." />
    <CardBody>
      {!displayedResult ? <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-center">
        <div><p className="text-sm text-slate-600 dark:text-slate-300">Текущая рыночная цена</p><p className="mt-1 text-2xl font-semibold tabular-nums">{formatMoney(currentPrice, currency, 2)}</p></div>
        <Button size="lg" onClick={analyze} disabled={loading || latest.isLoading} className="w-full sm:w-auto">{loading ? "Подготовка и расчёт…" : "Рассчитать справедливую стоимость"}</Button>
        {loading ? <p className="text-xs text-slate-500 sm:col-span-2">Подготавливаем данные · проверяем отчётность · рассчитываем три сценария</p> : null}
        {error ? <div role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 sm:col-span-2 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">{error}</div> : null}
      </div> : <div className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2"><div><p className="text-xs uppercase tracking-wide text-slate-500">Текущая цена</p><p className="text-xl font-semibold tabular-nums">{formatMoney(displayedResult.current_price, displayedResult.currency, 2)}</p></div><p className="text-xs text-slate-500">{displayedResult.cache_hit || !result ? "Готовая актуальная модель" : "Новая модель"} · данные {formatDate(displayedResult.data_as_of)}</p></div>
        {displayedResult.stale_due_to_new_financials ? <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">Вышла новая отчётность. Обновите оценку.</p> : null}
        <div className="grid grid-cols-3 gap-2">{SCENARIOS.map(({key,label,tone}) => <Scenario key={key} label={label} value={displayedResult.scenarios[key]} currency={displayedResult.currency} tone={tone} showDifference={settings?.show_dcf_scenario_differences !== false} />)}</div>
        <TwoYearChanges data={displayedResult.financial_changes_2y} currency={displayedResult.currency} />
        {settings?.show_dcf_explanation !== false ? <details className="rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-700">
          <summary className="cursor-pointer font-semibold">Почему такая оценка?</summary>
          <p className="mt-2 text-xs leading-relaxed text-slate-500">{displayedResult.explanation.summary}</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">{displayedResult.explanation.drivers.map(driver => <div key={driver.label} className="flex justify-between gap-3 text-xs"><span className="text-slate-500">{driver.label}</span><strong>{formatRate(driver.value)}</strong></div>)}</div>
          <ul className="mt-3 list-disc space-y-1 pl-4 text-xs text-slate-500">{displayedResult.explanation.risks.map(risk => <li key={risk}>{risk}</li>)}</ul>
        </details> : null}
        <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-slate-200 pt-3 text-xs text-slate-500 dark:border-slate-700">{settings?.show_dcf_confidence !== false ? <><span>Уверенность: <strong className="text-slate-700 dark:text-slate-200">{confidenceLabel(displayedResult.analysis_confidence)}</strong></span><span>Неопределённость: {uncertaintyLabel(displayedResult.valuation_uncertainty)}</span></> : null}<span>Готовность: {displayedResult.data_quality_status === "READY" ? "готово" : "готово с предупреждениями"}</span><span>Качество данных: {Math.round(displayedResult.data_quality_score * 100)}%</span><span>Расчёты доступны бесплатно и без лимита</span></div>
        {displayedResult.warnings.map(warning => <p key={warning} className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">{warning}</p>)}
        <p className="text-[11px] leading-relaxed text-slate-500">{displayedResult.disclaimer}</p>
      </div>}
    </CardBody>
  </Card></section>;
}

function Scenario({label,value,currency,tone,showDifference}:{label:string;value:DCFScenarioValue|undefined;currency:string;tone:string;showDifference:boolean}) {
  return <div className="rounded-xl border border-slate-200 bg-white/80 p-3 dark:border-slate-700 dark:bg-slate-900/70"><p className="truncate text-[11px] text-slate-500">{label}</p><p className="mt-1 text-sm font-semibold tabular-nums sm:text-lg">{formatMoney(value?.fair_value ?? null,currency,0)}</p>{showDifference ? <p className={`mt-1 text-xs font-semibold tabular-nums ${tone}`}>{value?.difference_percent == null ? "—" : `${value.difference_percent > 0 ? "+" : ""}${value.difference_percent}%`}</p> : null}</div>;
}

function TwoYearChanges({data,currency}:{data:DCFFinancialChanges2Y;currency:string}) {
  if (data.status !== "complete" || data.periods.length !== 2 || !data.changes) {
    return <div className="rounded-xl border border-slate-200 p-3 text-xs text-slate-500 dark:border-slate-700">Для сравнения изменений за два года пока недостаточно опубликованных годовых отчётов.</div>;
  }
  const [previous,current] = data.periods;
  const fields: Array<{label:string;field: keyof DCFFinancialPeriod;change: keyof NonNullable<DCFFinancialChanges2Y["changes"]>;rate?:boolean}> = [
    {label:"Выручка",field:"revenue",change:"revenue_change"},
    {label:"Операционная прибыль",field:"operating_profit",change:"operating_profit_change"},
    {label:"EBITDA",field:"ebitda",change:"ebitda_change"},
    {label:"Операционный денежный поток",field:"operating_cash_flow",change:"operating_cash_flow_change"},
    {label:"Свободный денежный поток",field:"free_cash_flow",change:"free_cash_flow_change"},
    {label:"Capex",field:"capex",change:"capex_change"},
    {label:"Чистый долг",field:"net_debt",change:"net_debt_change"},
    {label:"Маржа EBIT",field:"ebit_margin",change:"ebit_margin_change",rate:true},
  ];
  return <section className="rounded-xl border border-slate-200 bg-white/70 p-3 dark:border-slate-700 dark:bg-slate-900/60">
    <div className="mb-3 flex flex-wrap items-end justify-between gap-2"><div><h4 className="text-sm font-semibold">Фактические изменения за 2 года</h4><p className="text-xs text-slate-500">{formatDate(previous.period_end)} → {formatDate(current.period_end)}</p></div><p className="text-[11px] text-slate-500">По опубликованной отчётности · без прогнозных значений</p></div>
    <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">{fields.map(item => <FinancialChange key={item.field} label={item.label} previous={previous[item.field] as number|null} current={current[item.field] as number|null} change={data.changes?.[item.change] ?? null} currency={currency} rate={item.rate}/>)}</div>
  </section>;
}

function FinancialChange({label,previous,current,change,currency,rate=false}:{label:string;previous:number|null;current:number|null;change:number|null;currency:string;rate?:boolean}) {
  const value = (number:number|null) => rate ? formatRate(number) : `${formatCompact(number)} ${currency === "KZT" ? "₸" : currency}`;
  const movement = change == null ? "—" : rate ? `${change > 0 ? "+" : ""}${(change*100).toFixed(1)} п.п.` : `${change > 0 ? "+" : ""}${(change*100).toFixed(1)}%`;
  const tone = change == null ? "text-slate-500" : change > 0 ? "text-emerald-600 dark:text-emerald-400" : change < 0 ? "text-rose-600 dark:text-rose-400" : "text-slate-500";
  return <div><div className="flex items-center justify-between gap-2"><p className="truncate text-xs text-slate-500">{label}</p><strong className={`text-xs tabular-nums ${tone}`}>{movement}</strong></div><p className="mt-0.5 text-sm tabular-nums"><span className="text-slate-400">{value(previous)}</span><span className="mx-1.5">→</span><strong>{value(current)}</strong></p></div>;
}

function confidenceLabel(value: DCFResult["analysis_confidence"]) { return value === "high" ? "высокая" : value === "medium" ? "средняя" : "низкая"; }
function uncertaintyLabel(value: DCFResult["valuation_uncertainty"]) { return value === "high" ? "высокая" : value === "medium" ? "средняя" : value === "low" ? "низкая" : "—"; }
