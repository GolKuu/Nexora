"use client";

import { useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { stocksService } from "@/services/stocks";
import { useSettings } from "@/hooks/useSettings";
import { ApiError } from "@/services/client";
import type { DCFResult, DCFScenarioValue } from "@/types/api";
import { formatDate, formatMoney } from "@/utils/format";

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
    <CardHeader title="AI DCF Valuation" subtitle="Расчёт ИИ по опубликованной финансовой отчётности. Не является индивидуальной инвестиционной рекомендацией." />
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
        <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-slate-200 pt-3 text-xs text-slate-500 dark:border-slate-700">{settings?.show_dcf_confidence !== false ? <span>Уверенность расчёта: <strong className="text-slate-700 dark:text-slate-200">{confidenceLabel(displayedResult.analysis_confidence)}</strong></span> : null}<span>Отчётность на {formatDate(displayedResult.analysis_date)}</span><span>Цена на {formatDate(displayedResult.current_price_timestamp)}</span><span>Версия модели: {displayedResult.model_version}</span></div>
        {displayedResult.warnings.map(warning => <p key={warning} className="rounded-lg bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">{warning}</p>)}
        <p className="text-[11px] leading-relaxed text-slate-500">{displayedResult.disclaimer}</p>
      </div>}
    </CardBody>
  </Card></section>;
}

function Scenario({label,value,currency,tone,showDifference}:{label:string;value:DCFScenarioValue|undefined;currency:string;tone:string;showDifference:boolean}) {
  return <div className="rounded-xl border border-slate-200 bg-white/80 p-3 dark:border-slate-700 dark:bg-slate-900/70"><p className="truncate text-[11px] text-slate-500">{label}</p><p className="mt-1 text-sm font-semibold tabular-nums sm:text-lg">{formatMoney(value?.fair_value ?? null,currency,0)}</p>{showDifference ? <p className={`mt-1 text-xs font-semibold tabular-nums ${tone}`}>{value?.difference_percent == null ? "—" : `${value.difference_percent > 0 ? "+" : ""}${value.difference_percent}%`}</p> : null}</div>;
}

function confidenceLabel(value: DCFResult["analysis_confidence"]) { return value === "high" ? "высокая" : value === "medium" ? "средняя" : "низкая"; }
