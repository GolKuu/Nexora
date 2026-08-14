"use client";

import { useState } from "react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { stocksService } from "@/services/stocks";
import type { StockCalculation } from "@/types/api";
import { formatMoney, formatPercent } from "@/utils/format";

export function StockCalculator({ ticker, currency }: { ticker: string; currency: string }) {
  const [amount, setAmount] = useState("5000000"); const [scenario, setScenario] = useState("base");
  const [result, setResult] = useState<StockCalculation | null>(null); const [loading, setLoading] = useState(false);
  async function calculate() { setLoading(true); try { setResult(await stocksService.calculate(ticker, Number(amount), scenario)); } finally { setLoading(false); } }
  return <Card><CardHeader title="Что будет с моей суммой?" subtitle="Цена будущего периода — сценарий, не прогноз." /><CardBody className="space-y-3">
    <label className="text-xs text-slate-500">Сумма<input value={amount} onChange={(e) => setAmount(e.target.value.replace(/\D/g, ""))} className="mt-1 h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-base dark:border-slate-700" /></label>
    <div className="grid grid-cols-3 gap-1">{[["poor", "Плохо"], ["base", "Базово"], ["good", "Хорошо"]].map(([code, label]) => <button key={code} onClick={() => setScenario(code)} className={`rounded-lg px-2 py-2 text-xs ${scenario === code ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 dark:bg-slate-800"}`}>{label}</button>)}</div>
    <button onClick={calculate} disabled={loading || Number(amount) <= 0} className="h-11 w-full rounded-xl bg-emerald-600 text-sm font-semibold text-white disabled:opacity-50">{loading ? "Считаем…" : "Рассчитать"}</button>
    {result ? <div className="space-y-2 border-t border-slate-100 pt-3 text-sm dark:border-slate-800"><Row label="Количество" value={`${result.quantity} шт.`} /><Row label="Цена расчета" value={`${formatMoney(result.unit_price, currency, 2)} · ${result.calculation_price_type}`} /><Row label="Покупка с комиссией" value={formatMoney(result.total_purchase_cost, currency, 2)} /><Row label="Остаток" value={formatMoney(result.cash_remaining, currency, 2)} /><Row label="Trailing дивиденды" value={formatMoney(result.dividend_income_trailing, currency, 2)} /><Row label="Сценарная прибыль" value={formatMoney(result.scenario_profit, currency, 2)} /><Row label="Сценарная доходность" value={formatPercent(result.total_return_percent)} />{result.warnings.map((warning) => <p key={warning} className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">{warning}</p>)}</div> : null}
  </CardBody></Card>;
}
function Row({label, value}: {label: string; value: string}) { return <div className="flex justify-between gap-3"><span className="text-slate-500">{label}</span><span className="tabular text-right font-medium">{value}</span></div>; }
