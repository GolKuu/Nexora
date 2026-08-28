"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { alertsService } from "@/services/user";

const KINDS = [
  ["price_below", "Цена ниже"], ["price_above", "Цена выше"], ["pe_below", "P/E ниже"],
  ["dividend_announced", "Объявлен дивиденд"], ["financial_report", "Новая отчётность"],
  ["profit_change", "Сильное изменение прибыли"], ["score_change", "Изменение score"], ["company_news", "Новости компании"],
  ["price_approaches_support", "Цена приближается к поддержке"], ["support_broken", "Поддержка пробита"],
  ["resistance_broken", "Сопротивление пробито"], ["golden_cross", "Golden Cross"], ["death_cross", "Death Cross"],
  ["rsi_extreme", "Экстремум RSI"], ["volume_spike", "Всплеск объёма"], ["technical_risk_changed", "Изменение технического риска"],
] as const;

export function StockAlerts({ ticker }: { ticker: string }) {
  const { data } = useSWR("alerts", () => alertsService.list(), { revalidateOnFocus: false });
  const [kind, setKind] = useState("price_below");
  const [threshold, setThreshold] = useState("");
  const thresholdRequired = ["price_below", "price_above", "pe_below"].includes(kind);
  const rows = data?.items.filter((item) => item.instrument_type === "stock" && item.ticker === ticker) ?? [];

  async function add() {
    await alertsService.addStock(ticker, kind, thresholdRequired ? Number(threshold) : undefined);
    setThreshold(""); await mutate("alerts");
  }

  return <Card><CardHeader title="Алерты" subtitle="Срабатывают только после подтверждённого изменения данных." /><CardBody className="space-y-3"><select value={kind} onChange={(event) => setKind(event.target.value)} className="h-10 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700">{KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>{thresholdRequired ? <input type="number" value={threshold} onChange={(event) => setThreshold(event.target.value)} placeholder="Порог" className="h-10 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700" /> : null}<button onClick={() => void add()} disabled={thresholdRequired && !threshold} className="h-10 w-full rounded-xl bg-slate-900 text-sm font-semibold text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900">Добавить алерт</button>
    {rows.map((row) => <div key={row.id} className="flex items-center justify-between gap-2 border-t border-slate-100 pt-2 text-xs dark:border-slate-800"><button onClick={async () => { await alertsService.update(row.id, !row.is_active); await mutate("alerts"); }} className={row.is_active ? "text-emerald-600" : "text-slate-400"}>{row.is_active ? "●" : "○"} {KINDS.find(([value]) => value === row.kind)?.[1] ?? row.kind}{row.threshold == null ? "" : ` ${row.threshold}`}</button><button onClick={async () => { await alertsService.remove(row.id); await mutate("alerts"); }} className="text-rose-500">удалить</button></div>)}
  </CardBody></Card>;
}
