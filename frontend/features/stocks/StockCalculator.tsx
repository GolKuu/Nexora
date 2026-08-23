"use client";

import { useEffect, useRef, useState } from "react";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { stocksService } from "@/services/stocks";
import type { StockCalculation } from "@/types/api";
import { formatMoney, formatPercent } from "@/utils/format";

export function StockCalculator({ ticker, currency }: { ticker: string; currency: string }) {
  const [mode, setMode] = useState<"amount" | "quantity">("amount");
  const [input, setInput] = useState("5000000");
  const [scenario, setScenario] = useState("base");
  const [commission, setCommission] = useState("0.1");
  const [commissionType, setCommissionType] = useState<"percent" | "fixed">("percent");
  const [result, setResult] = useState<StockCalculation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  useEffect(() => {
    const value = Number(input);
    if (!(value > 0)) { setResult(null); return; }
    const timer = window.setTimeout(async () => {
      const id = ++requestId.current;
      setLoading(true); setError(null);
      try {
        const next = await stocksService.calculate(ticker, { mode, value, scenario, commission: Number(commission) || 0, commissionType });
        if (id === requestId.current) setResult(next);
      } catch (reason) {
        if (id === requestId.current) setError(reason instanceof Error ? reason.message : "Не удалось рассчитать");
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    }, 350);
    return () => window.clearTimeout(timer);
  }, [commission, commissionType, input, mode, scenario, ticker]);

  return <Card><CardHeader title="Что будет с моей суммой?" subtitle="Пересчёт выполняется автоматически. Будущая цена — сценарий, не обещание."/><CardBody className="space-y-3">
    <div className="grid grid-cols-2 gap-1">{[["amount","По сумме"],["quantity","По количеству"]].map(([code,label])=><button key={code} onClick={()=>{setMode(code as "amount"|"quantity");setInput(code === "amount" ? "5000000" : "100");}} className={`rounded-lg px-2 py-2 text-xs ${mode === code ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 dark:bg-slate-800"}`}>{label}</button>)}</div>
    <label className="text-xs text-slate-500">{mode === "amount" ? "Сумма" : "Количество акций"}<input value={input} inputMode="decimal" onChange={e=>setInput(e.target.value.replace(/[^\d.,]/g, "").replace(",", "."))} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-base dark:border-slate-700"/></label>
    <div className="grid grid-cols-[1fr_7rem] gap-2"><label className="text-xs text-slate-500">Комиссия<input value={commission} inputMode="decimal" onChange={e=>setCommission(e.target.value.replace(/[^\d.,]/g, "").replace(",", "."))} className="mt-1 h-10 w-full rounded-xl border border-slate-200 bg-transparent px-3 dark:border-slate-700"/></label><label className="text-xs text-slate-500">Тип<select value={commissionType} onChange={e=>setCommissionType(e.target.value as "percent"|"fixed")} className="mt-1 h-10 w-full rounded-xl border border-slate-200 bg-transparent px-2 dark:border-slate-700"><option value="percent">%</option><option value="fixed">₸</option></select></label></div>
    <div className="grid grid-cols-3 gap-1">{[["poor","Плохо"],["base","Базово"],["good","Хорошо"]].map(([code,label])=><button key={code} onClick={()=>setScenario(code)} className={`rounded-lg px-2 py-2 text-xs ${scenario === code ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 dark:bg-slate-800"}`}>{label}</button>)}</div>
    <p className="text-xs text-slate-400">{loading ? "Обновляем расчёт…" : "Расчёт актуален для введённых параметров"}</p>
    {error ? <p className="text-sm text-rose-600">{error}</p> : null}
    {result ? <div className="space-y-2 border-t border-slate-100 pt-3 text-sm dark:border-slate-800"><Row label="Количество" value={`${result.quantity} шт.`}/><Row label="Цена расчета" value={`${formatMoney(result.unit_price,currency,2)} · ${result.calculation_price_type}`}/><Row label="Покупка" value={formatMoney(result.principal_cost,currency,2)}/><Row label="Комиссия" value={formatMoney(result.commission,currency,2)}/><Row label="Итого" value={formatMoney(result.total_purchase_cost,currency,2)}/><Row label="Остаток" value={formatMoney(result.cash_remaining,currency,2)}/><Row label="Trailing дивиденды" value={formatMoney(result.dividend_income_trailing,currency,2)}/><Row label="Сценарная прибыль" value={formatMoney(result.scenario_profit,currency,2)}/><Row label="Сценарная доходность" value={formatPercent(result.total_return_percent)}/>{result.liquidity_warning ? <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">{result.liquidity_warning}</p> : null}{result.warnings.map(warning=><p key={warning} className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">{warning}</p>)}</div> : null}
  </CardBody></Card>;
}

function Row({label,value}:{label:string;value:string}) { return <div className="flex justify-between gap-3"><span className="text-slate-500">{label}</span><span className="tabular text-right font-medium">{value}</span></div>; }
