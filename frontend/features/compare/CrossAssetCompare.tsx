"use client";

import { useState } from "react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { stocksService } from "@/services/stocks";
import type { CrossAssetCompareResponse } from "@/types/api";
import { formatNumber, formatRate } from "@/utils/format";

export function CrossAssetCompare() {
  const [stock, setStock] = useState("HSBK");
  const [bond, setBond] = useState("");
  const [result, setResult] = useState<CrossAssetCompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function compare() {
    try {
      setError(null);
      setResult(await stocksService.compareCrossAsset([
        { identifier: stock.trim(), instrument_type: "stock" },
        { identifier: bond.trim(), instrument_type: "bond" },
      ]));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось выполнить cross-asset сравнение");
    }
  }

  return <Card><CardHeader title="Акция и облигация" subtitle="Общие характеристики без смешивания YTM и сценарного роста цены." /><CardBody className="space-y-4">
    <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]"><input value={stock} onChange={(event) => setStock(event.target.value)} placeholder="Тикер акции" className="h-11 rounded-xl border border-slate-200 bg-transparent px-3 dark:border-slate-700" /><input value={bond} onChange={(event) => setBond(event.target.value)} placeholder="Тикер облигации" className="h-11 rounded-xl border border-slate-200 bg-transparent px-3 dark:border-slate-700" /><button onClick={() => void compare()} disabled={!stock.trim() || !bond.trim()} className="rounded-xl bg-slate-900 px-4 text-sm font-semibold text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900">Сравнить классы</button></div>
    {error ? <p className="text-sm text-rose-600">{error}</p> : null}
    {result ? <><p className="text-sm text-slate-600 dark:text-slate-300">{result.explanation}</p><div className="overflow-x-auto"><table className="w-full min-w-[620px] text-sm"><thead><tr><th className="p-2 text-left">Характеристика</th>{result.items.map((item) => <th key={`${item.instrument_type}-${item.ticker}`} className="p-2 text-right">{item.ticker} · {item.instrument_type === "stock" ? "Акция" : "Облигация"}</th>)}</tr></thead><tbody>
      <Row label="Риск" values={result.items.map((item) => formatNumber(item.risk.value, 0))} /><Row label="Ликвидность" values={result.items.map((item) => formatNumber(item.liquidity.value, 0))} /><Row label="Доход от выплат" values={result.items.map((item) => item.instrument_type === "stock" ? formatRate(item.potential_income.dividend_yield_trailing) : formatRate(item.potential_income.ytm))} /><Row label="Горизонт" values={result.items.map((item) => item.horizon ?? "выбирает инвестор")} /><Row label="Волатильность" values={result.items.map((item) => formatRate(item.volatility))} /><Row label="Предсказуемость cash flow" values={result.items.map((item) => item.cashflow_predictability)} />
    </tbody></table></div><p className="text-xs text-amber-700 dark:text-amber-300">{result.warning}</p></> : null}
  </CardBody></Card>;
}

function Row({ label, values }: { label: string; values: string[] }) {
  return <tr className="border-t border-slate-100 dark:border-slate-800"><td className="p-2 text-slate-500">{label}</td>{values.map((value, index) => <td key={`${label}-${index}`} className="p-2 text-right">{value}</td>)}</tr>;
}
