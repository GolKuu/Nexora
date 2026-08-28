"use client";

import { useState } from "react";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { stocksService } from "@/services/stocks";
import type { StockCalculation, StockCard, TechnicalAnalysisResponse } from "@/types/api";
import { formatMoney, formatNumber, formatRate } from "@/utils/format";

type TechnicalSummary = Pick<TechnicalAnalysisResponse,"trend"|"rsi"|"technical_risk"|"technical_momentum_score"|"as_of">;
type Column = StockCard & {investment_calculation:StockCalculation|null;dcf_summary:{status:string;bear_fair_value?:number|null;base_fair_value?:number|null;bull_fair_value?:number|null;base_difference_percent?:number|null;analysis_confidence?:string|null};technical_summary?:TechnicalSummary};

export function StockCompare(){
  const [tickers,setTickers]=useState("HSBK, KCEL"),[amount,setAmount]=useState("5000000"),[columns,setColumns]=useState<Column[]>([]),[error,setError]=useState<string|null>(null);
  async function compare(){try{setError(null);const ids=tickers.split(/[,\s]+/).filter(Boolean).slice(0,10);const result=await stocksService.compare(ids,Number(amount));setColumns(result.columns)}catch(e){setError(e instanceof Error?e.message:"Не удалось сравнить")}}
  const rows:Array<[string,(column:Column)=>string]>=[
    ["Цена",column=>formatMoney(column.price,column.currency,2)],
    ["Технический тренд",column=>column.technical_summary?.trend.state??"—"],
    ["RSI14",column=>formatNumber(column.technical_summary?.rsi.value,1)],
    ["Технический риск",column=>column.technical_summary?.technical_risk.label??"—"],
    ["Технический импульс",column=>column.technical_summary?`${column.technical_summary.technical_momentum_score.value}/100`:"—"],
    ["DCF статус",column=>column.dcf_summary.status],
    ["Bear Fair Value",column=>formatMoney(column.dcf_summary.bear_fair_value,column.currency,0)],
    ["Base Fair Value",column=>formatMoney(column.dcf_summary.base_fair_value,column.currency,0)],
    ["Bull Fair Value",column=>formatMoney(column.dcf_summary.bull_fair_value,column.currency,0)],
    ["Base vs рынок",column=>column.dcf_summary.base_difference_percent==null?"—":`${column.dcf_summary.base_difference_percent>0?"+":""}${formatNumber(column.dcf_summary.base_difference_percent,1)}%`],
    ["DCF уверенность",column=>column.dcf_summary.analysis_confidence??"—"],
    ["Market Cap",column=>formatMoney(column.market_cap,column.currency)], ["P/E",column=>formatNumber(column.metrics.pe)],
    ["P/B",column=>formatNumber(column.metrics.pb)], ["EV/EBITDA",column=>formatNumber(column.metrics.ev_ebitda)],
    ["ROE",column=>formatRate(column.metrics.roe)], ["Revenue Growth",column=>formatRate(column.metrics.revenue_growth)],
    ["Profit Growth",column=>formatRate(column.metrics.earnings_growth)], ["Dividend Yield",column=>formatRate(column.metrics.trailing_dividend_yield)],
    ["Quality",column=>formatNumber(column.scores.quality?.value,0)], ["Valuation",column=>formatNumber(column.scores.valuation?.value,0)],
    ["Growth",column=>formatNumber(column.scores.growth?.value,0)], ["Liquidity",column=>formatNumber(column.scores.liquidity?.value,0)],
    ["Risk",column=>formatNumber(column.scores.risk?.value,0)], ["Investment Score",column=>formatNumber(column.scores.investment?.value,0)],
    ["Количество",column=>column.investment_calculation?`${column.investment_calculation.quantity}`:"—"],
    ["Остаток",column=>formatMoney(column.investment_calculation?.cash_remaining,column.currency)],
    ["Сценарный результат",column=>formatMoney(column.investment_calculation?.scenario_profit,column.currency)],
  ];
  return <Card><CardHeader title="Сравнение акций" subtitle="До 10 акций; фундаментальная оценка и техническая картина показаны отдельно."/><CardBody className="space-y-4"><div className="grid gap-2 sm:grid-cols-[1fr_12rem_auto]"><input value={tickers} onChange={event=>setTickers(event.target.value)} placeholder="HSBK, KCEL" className="h-11 rounded-xl border border-slate-200 bg-transparent px-3 dark:border-slate-700"/><input value={amount} onChange={event=>setAmount(event.target.value.replace(/\D/g,""))} className="h-11 rounded-xl border border-slate-200 bg-transparent px-3 dark:border-slate-700"/><button onClick={compare} className="rounded-xl bg-emerald-600 px-4 text-sm font-semibold text-white">Сравнить</button></div>{error?<p className="text-sm text-rose-600">{error}</p>:null}{columns.length?<div className="overflow-x-auto"><table className="w-full min-w-[700px] text-sm"><thead><tr><th className="p-2 text-left text-slate-500">Показатель</th>{columns.map(column=><th key={column.ticker} className="p-2 text-right">{column.ticker}</th>)}</tr></thead><tbody>{rows.map(([label,render])=><tr key={label} className="border-t border-slate-100 dark:border-slate-800"><td className="p-2 text-slate-500">{label}</td>{columns.map(column=><td key={column.ticker} className="tabular p-2 text-right">{render(column)}</td>)}</tr>)}</tbody></table></div>:null}</CardBody></Card>;
}
