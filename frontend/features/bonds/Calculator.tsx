"use client";

import { useEffect, useRef, useState } from "react";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Stat } from "@/components/ui/Stat";
import { bondsService } from "@/services/bonds";
import type { BondInvestmentCalculation } from "@/types/api";
import { formatMoney, formatPercent, formatYears } from "@/utils/format";

export function Calculator({ ticker, currency }: { ticker: string; currency: string }) {
  const [mode, setMode] = useState<"amount" | "quantity">("amount");
  const [input, setInput] = useState("1000000");
  const [commission, setCommission] = useState("0.1");
  const [commissionType, setCommissionType] = useState<"percent" | "fixed">("percent");
  const [inflationEnabled, setInflationEnabled] = useState(true);
  const [exitMode, setExitMode] = useState<"maturity" | "date">("maturity");
  const [exitDate, setExitDate] = useState("");
  const [scenario, setScenario] = useState<"bad" | "base" | "good">("base");
  const [result, setResult] = useState<BondInvestmentCalculation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  useEffect(() => {
    const value = Number(input);
    if (!(value > 0) || (exitMode === "date" && !exitDate)) { setResult(null); return; }
    const timer = window.setTimeout(async () => {
      const id = ++requestId.current; setLoading(true); setError(null);
      try {
        const next = await bondsService.calculateInvestment(ticker, { mode, value, commission: Number(commission) || 0, commissionType, inflationEnabled, exitMode, exitDate, scenario });
        if (id === requestId.current) setResult(next);
      } catch (reason) {
        if (id === requestId.current) setError(reason instanceof Error ? reason.message : "Не удалось посчитать");
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    }, 350);
    return () => window.clearTimeout(timer);
  }, [commission, commissionType, exitDate, exitMode, inflationEnabled, input, mode, scenario, ticker]);

  return <Card><CardHeader title="Калькулятор облигации" subtitle="Автоматический расчёт покупки, выплат, продажи и реального результата."/><CardBody className="space-y-3">
    <div className="grid grid-cols-2 gap-1">{[["amount","По сумме"],["quantity","По количеству"]].map(([code,label])=><button key={code} onClick={()=>{setMode(code as "amount"|"quantity");setInput(code === "amount" ? "1000000" : "10");}} className={`rounded-lg px-2 py-2 text-xs ${mode === code ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 dark:bg-slate-800"}`}>{label}</button>)}</div>
    <label className="text-xs text-slate-500">{mode === "amount" ? "Сумма" : "Количество облигаций"}<input value={input} inputMode="decimal" onChange={e=>setInput(e.target.value.replace(/[^\d.,]/g, "").replace(",", "."))} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-base dark:border-slate-700"/></label>
    <div className="grid grid-cols-[1fr_7rem] gap-2"><label className="text-xs text-slate-500">Комиссия<input value={commission} inputMode="decimal" onChange={e=>setCommission(e.target.value.replace(/[^\d.,]/g, "").replace(",", "."))} className="mt-1 h-10 w-full rounded-xl border border-slate-200 bg-transparent px-3 dark:border-slate-700"/></label><label className="text-xs text-slate-500">Тип<select value={commissionType} onChange={e=>setCommissionType(e.target.value as "percent"|"fixed")} className="mt-1 h-10 w-full rounded-xl border border-slate-200 bg-transparent px-2 dark:border-slate-700"><option value="percent">%</option><option value="fixed">₸</option></select></label></div>
    <div className="grid grid-cols-3 gap-1">{[["bad","Негативный"],["base","Базовый"],["good","Позитивный"]].map(([code,label])=><button key={code} onClick={()=>setScenario(code as typeof scenario)} className={`rounded-lg px-2 py-2 text-xs ${scenario === code ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 dark:bg-slate-800"}`}>{label}</button>)}</div>
    <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300"><input type="checkbox" checked={inflationEnabled} onChange={e=>setInflationEnabled(e.target.checked)}/>учитывать инфляцию</label>
    <div className="grid grid-cols-2 gap-2"><button onClick={()=>setExitMode("maturity")} className={`rounded-lg px-2 py-2 text-xs ${exitMode === "maturity" ? "bg-emerald-600 text-white" : "bg-slate-100 dark:bg-slate-800"}`}>До погашения</button><button onClick={()=>setExitMode("date")} className={`rounded-lg px-2 py-2 text-xs ${exitMode === "date" ? "bg-emerald-600 text-white" : "bg-slate-100 dark:bg-slate-800"}`}>Продать раньше</button></div>
    {exitMode === "date" ? <label className="text-xs text-slate-500">Дата продажи<input type="date" value={exitDate} onChange={e=>setExitDate(e.target.value)} className="mt-1 h-10 w-full rounded-xl border border-slate-200 bg-transparent px-3 dark:border-slate-700"/></label> : null}
    <p className="text-xs text-slate-400">{loading ? "Обновляем расчёт…" : "Пересчёт выполняется без кнопки Calculate"}</p>
    {error ? <p className="text-sm text-rose-600">{error}</p> : null}
    {result ? <div className="space-y-4 border-t border-slate-100 pt-3 dark:border-slate-800"><div className="grid grid-cols-2 gap-3"><Stat label="Количество" value={`${result.quantity} шт.`} hint={`dirty ${formatMoney(result.unit_dirty_price,currency,2)}`}/><Stat label="Стоимость покупки" value={formatMoney(result.total_purchase_cost,currency)} hint={`комиссия ${formatMoney(result.commission,currency,2)}`}/><Stat label="Остаток" value={formatMoney(result.cash_remaining,currency)}/><Stat label="Купоны" value={formatMoney(result.coupon_income,currency)}/><Stat label="Возврат номинала" value={formatMoney(result.principal_repayment,currency)}/><Stat label="Всего получено" value={formatMoney(result.total_cash_received,currency)}/><Stat label="Прибыль" value={formatMoney(result.total_profit,currency)} tone={(result.total_profit ?? 0) >= 0 ? "positive" : "negative"}/><Stat label="Годовая доходность" value={formatPercent(result.annualized_return_percent)} hint={formatYears(result.holding_period_years)}/><Stat label="Реальная прибыль" value={formatMoney(result.real_profit,currency)} tone={(result.real_profit ?? 0) >= 0 ? "positive" : "negative"}/><Stat label="После инфляции" value={formatPercent(result.real_annualized_return_percent)} hint={result.inflation_source ?? undefined}/></div>{result.liquidity_warning ? <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">{result.liquidity_warning}</p> : null}{result.warnings.map(warning=><p key={warning} className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">{warning}</p>)}</div> : null}
  </CardBody></Card>;
}
