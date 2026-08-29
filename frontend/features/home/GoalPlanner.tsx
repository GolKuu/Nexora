"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { goalPlannerService, type GoalPlanInput } from "@/services/user";
import type { GoalPlan } from "@/types/api";
import { cn } from "@/utils/cn";
import { formatMoney, formatRate } from "@/utils/format";

const PROFILES: Array<[GoalPlanInput["risk_profile"], string, string]> = [
  ["conservative", "Осторожный", "Больше качественных облигаций"],
  ["balanced", "Баланс", "Акции и облигации"],
  ["growth", "Рост", "Больше акций, лимиты сохраняются"],
  ["income", "Доход", "Купоны и дивиденды"],
];
const FEASIBILITY = {
  FEASIBLE: ["Реалистичная", "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"],
  CHALLENGING: ["Требует внимания", "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"],
  HIGH_RISK: ["Высокий риск", "bg-orange-50 text-orange-700 dark:bg-orange-950/40 dark:text-orange-300"],
  UNREALISTIC: ["Слишком агрессивная", "bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300"],
} as const;
const onlyDigits = (value: string) => value.replace(/\D/g, "");

export function GoalPlanner() {
  const [capital, setCapital] = useState("5000000");
  const [target, setTarget] = useState("5500000");
  const [horizon, setHorizon] = useState("12");
  const [contribution, setContribution] = useState("0");
  const [targetType, setTargetType] = useState<GoalPlanInput["target_type"]>("FINAL_VALUE");
  const [profile, setProfile] = useState<GoalPlanInput["risk_profile"]>("balanced");
  const [plan, setPlan] = useState<GoalPlan | null>(null);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);

  async function build(nextExcluded = excluded) {
    setBusy(true); setError(null); setCopied(null);
    try {
      setPlan(await goalPlannerService.plan({ starting_capital: Number(capital), target_type: targetType,
        target_amount: Number(target), horizon_months: Number(horizon), monthly_contribution: Number(contribution),
        risk_profile: profile, currency: "KZT", excluded_instruments: nextExcluded }));
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Не удалось построить план"); }
    finally { setBusy(false); }
  }

  return <div className="space-y-5">
    <section className="rounded-3xl border border-slate-200 bg-gradient-to-br from-white to-emerald-50/70 p-5 shadow-sm dark:border-slate-800 dark:from-slate-900 dark:to-emerald-950/20 sm:p-7">
      <p className="text-xs font-semibold uppercase tracking-[.2em] text-emerald-600">Investment Goal Planner</p>
      <h2 className="mt-2 text-2xl font-semibold tracking-tight">План достижения инвестиционной цели</h2>
      <p className="mt-2 text-sm text-slate-500">Реальные инструменты, целые лоты и прозрачные сценарии. Расчёты выполняются детерминированно, без LLM.</p>
      <div className="mt-6 grid gap-4 md:grid-cols-3">
        <Field label="КАПИТАЛ" hint="Сколько у вас есть сейчас?"><Input aria-label="Капитал" inputMode="numeric" value={capital} onChange={e => setCapital(onlyDigits(e.target.value))} /></Field>
        <Field label="ЦЕЛЬ" hint={targetType === "FINAL_VALUE" ? "Итоговая сумма" : "Желаемая прибыль"}><Input aria-label="Цель" inputMode="numeric" value={target} onChange={e => setTarget(onlyDigits(e.target.value))} /><div className="mt-2 flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800">{([["FINAL_VALUE","Итоговая сумма"],["PROFIT","Прибыль"]] as const).map(([value,label]) => <button key={value} type="button" onClick={() => setTargetType(value)} className={cn("flex-1 rounded-md px-2 py-1 text-[11px]", targetType === value && "bg-white font-semibold shadow-sm dark:bg-slate-700")}>{label}</button>)}</div></Field>
        <Field label="СРОК" hint="В месяцах"><Input aria-label="Срок" type="number" min="1" max="600" value={horizon} onChange={e => setHorizon(e.target.value)} /></Field>
      </div>
      <p className="mt-5 text-xs font-medium text-slate-500">ПРОФИЛЬ</p>
      <div className="mt-2 grid grid-cols-2 gap-2 lg:grid-cols-4">{PROFILES.map(([code,label,hint]) => <button key={code} type="button" onClick={() => setProfile(code)} className={cn("rounded-xl border p-3 text-left", profile === code ? "border-emerald-500 bg-emerald-50 dark:bg-emerald-950/40" : "border-slate-200 dark:border-slate-700")}><span className="block text-sm font-semibold">{label}</span><span className="mt-1 block text-[11px] text-slate-500">{hint}</span></button>)}</div>
      <div className="mt-5 grid items-end gap-4 sm:grid-cols-[1fr_auto]"><Field label="ЕЖЕМЕСЯЧНОЕ ПОПОЛНЕНИЕ" hint="необязательно"><Input aria-label="Ежемесячное пополнение" inputMode="numeric" value={contribution} onChange={e => setContribution(onlyDigits(e.target.value))} /></Field><Button className="h-11 px-7" disabled={busy || !Number(capital) || !Number(target) || !Number(horizon)} onClick={() => void build()}>{busy ? "Рассчитываем…" : "Построить план"}</Button></div>
      {busy && <p className="mt-4 rounded-xl bg-slate-900 p-3 text-sm text-white dark:bg-white dark:text-slate-900">Анализируем цель · проверяем ликвидность · строим сценарии и реинвестирование</p>}
      {error && <div role="alert" className="mt-4 flex justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"><span>{error}</span><button className="font-semibold" onClick={() => void build()}>Повторить</button></div>}
    </section>
    {plan && <PlanResult plan={plan} copied={copied} onReplace={setPlan} onExclude={async ticker => { const next=[...excluded,ticker]; setExcluded(next); await build(next); }} onCopy={async () => { if(plan.goal_id){ const result=await goalPlannerService.copyToPortfolio(plan.goal_id); setCopied(result.portfolio_id); } }} />}
  </div>;
}

function PlanResult({ plan, copied, onExclude, onCopy, onReplace }: { plan: GoalPlan; copied: number | null; onExclude:(ticker:string)=>Promise<void>; onCopy:()=>Promise<void>; onReplace:(plan:GoalPlan)=>void }) {
  const feasibility=FEASIBILITY[plan.feasibility];
  const [quantities,setQuantities]=useState<Record<string,string>>({});
  const [editing,setEditing]=useState(false);
  const [editError,setEditError]=useState<string|null>(null);
  useEffect(()=>setQuantities(Object.fromEntries(plan.initial_portfolio.map(row=>[row.ticker,String(row.quantity)]))),[plan]);
  return <div className="space-y-5">
    <Card><CardBody><div className="flex flex-wrap justify-between gap-4"><div><p className="text-xs uppercase tracking-wider text-slate-400">Оценка цели</p><h3 className="mt-1 text-xl font-semibold">Требуется {plan.required_return_pct.toLocaleString("ru-RU")} % годовых</h3><p className="mt-1 text-sm text-slate-500">Базовый ориентир с запасом: {formatMoney(plan.target.planner_base_target,"KZT")}</p></div><span className={cn("h-fit rounded-full px-3 py-1.5 text-sm font-semibold",feasibility[1])}>{feasibility[0]}</span></div>{["HIGH_RISK","UNREALISTIC"].includes(plan.feasibility) && <p className="mt-4 rounded-xl bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-950/30 dark:text-rose-300">Цель слишком агрессивная для выбранного срока и риска. Планировщик не повышает спекулятивную долю ради цели.</p>}</CardBody></Card>
    <div className="grid gap-3 sm:grid-cols-3">{(["negative","base","positive"] as const).map(key => { const s=plan.scenarios[key]; const label={negative:"Негативный",base:"Базовый",positive:"Позитивный"}[key]; return <div key={key} className={cn("rounded-2xl border p-4",key==="negative"?"border-rose-200 bg-rose-50/50 dark:border-rose-900":key==="base"?"border-emerald-300 bg-emerald-50/50 dark:border-emerald-800":"border-sky-200 bg-sky-50/50 dark:border-sky-900")}><p className="text-xs font-semibold uppercase text-slate-500">{label}</p><p className="mt-2 text-xl font-semibold">{formatMoney(s.final_value,"KZT")}</p><p className={cn("mt-2 text-xs font-semibold",s.target_reached?"text-emerald-600":"text-rose-600")}>{s.target_reached?"Цель достигается по модели":`Дефицит ${formatMoney(Math.abs(s.difference_vs_target),"KZT")}`}</p></div>})}</div>
    <Card><CardHeader title="Что купить сейчас" subtitle={`Исполнимые количества · cash ${formatMoney(plan.cash_remaining,"KZT")}`} /><CardBody><div className="grid gap-3 lg:grid-cols-2">{plan.initial_portfolio.map(item => <article key={`${item.instrument_type}-${item.ticker}`} className="rounded-2xl border border-slate-200 p-4 dark:border-slate-700"><div className="flex justify-between gap-3"><div><span className={cn("rounded px-2 py-1 text-[10px] font-bold",item.instrument_type==="stock"?"bg-emerald-100 text-emerald-700":"bg-sky-100 text-sky-700")}>{item.instrument_type==="stock"?"АКЦИЯ":"ОБЛИГАЦИЯ"}</span><Link className="ml-2 font-semibold hover:underline" href={`/${item.instrument_type==="stock"?"stock":"bond"}/${item.ticker}`}>{item.ticker}</Link><p className="mt-2 text-xs text-slate-500">{item.name}</p></div><button className="text-xs text-slate-400 hover:text-rose-600" onClick={() => void onExclude(item.ticker)}>исключить</button></div><div className="mt-4 grid grid-cols-2 gap-3 text-sm"><div><span className="block text-xs text-slate-400">Количество, кратно {item.lot_size}</span><Input aria-label={`Количество ${item.ticker}`} type="number" min="0" step={item.lot_size} value={quantities[item.ticker]??item.quantity} onChange={event=>setQuantities(value=>({...value,[item.ticker]:event.target.value}))}/></div><Metric label="Стоимость" value={formatMoney(item.purchase_cost,item.currency)}/><Metric label="Доля" value={`${(item.allocation*100).toFixed(1)}%`}/><Metric label="Ожид. доходность" value={formatRate(item.expected_return)}/></div><p className="mt-3 text-xs text-slate-500"><b>Почему:</b> {item.reason}</p><p className="mt-2 text-[11px] text-slate-400">Риск: {item.risk} · ликвидность: {item.liquidity==null?"нет оценки":`${Math.round(item.liquidity)}/100`}</p><TechnicalTiming item={item}/></article>)}</div><div className="mt-4 flex flex-wrap items-center gap-3"><Button disabled={editing||!plan.goal_id} onClick={async()=>{if(!plan.goal_id)return;setEditing(true);setEditError(null);try{onReplace(await goalPlannerService.edit(plan.goal_id,plan.initial_portfolio.map(row=>({ticker:row.ticker,quantity:Number(quantities[row.ticker]??row.quantity)}))));}catch(caught){setEditError(caught instanceof Error?caught.message:"Не удалось пересчитать план");}finally{setEditing(false);}}}>{editing?"Пересчитываем…":"Применить количества"}</Button><span className="text-xs text-slate-500">Cash, сценарии, риск и календарь пересчитаются; будет создана новая версия.</span></div>{editError&&<p className="mt-2 text-sm text-rose-600">{editError}</p>}</CardBody></Card>
    <Card><CardHeader title="План реинвестирования" subtitle="Новые деньги идут в недовес; автоматических продаж нет"/><CardBody><div className="space-y-2">{plan.reinvestment_plan.slice(0,8).map(step => <div key={step.month} className="grid gap-2 rounded-xl bg-slate-50 p-3 text-sm dark:bg-slate-800 sm:grid-cols-[100px_1fr_auto]"><strong>Месяц {step.month}</strong><span>{step.purchases.length?step.purchases.map(p=>`Купить ${p.quantity} ${p.ticker}`).join(", "):"Накапливать до целого лота"}</span><span className="text-slate-500">cash {formatMoney(step.cash_remaining,"KZT")}</span></div>)}</div></CardBody></Card>
    <Card><CardHeader title="Календарь денежных потоков" subtitle="Купоны договорные; оценки дивидендов помечены отдельно"/><CardBody className="overflow-x-auto"><table className="w-full min-w-[650px] text-sm"><thead><tr className="text-left text-xs uppercase text-slate-400"><th>Месяц</th><th>Пополнение</th><th>Купон</th><th>Дивиденд</th><th>Погашение</th><th>Реинвест.</th><th>Cash</th></tr></thead><tbody>{plan.cashflow_calendar.map(r=><tr key={r.month} className="border-t border-slate-100 dark:border-slate-800"><td className="py-2 font-medium">{r.month}</td><td>{formatMoney(r.contribution,"KZT")}</td><td>{formatMoney(r.coupon,"KZT")}</td><td>{formatMoney(r.dividend,"KZT")}</td><td>{formatMoney(r.principal,"KZT")}</td><td>{formatMoney(r.reinvested,"KZT")}</td><td>{formatMoney(r.cash_balance,"KZT")}</td></tr>)}</tbody></table></CardBody></Card>
    <Card><CardHeader title="Путь к цели"/><CardBody><div className="grid grid-cols-2 gap-4 sm:grid-cols-4"><Metric label="Старт" value={formatMoney(plan.target_progress.starting_capital,"KZT")}/><Metric label="Пополнения" value={formatMoney(plan.target_progress.contributions,"KZT")}/><Metric label="Купоны + дивиденды" value={formatMoney(plan.target_progress.coupon_income+plan.target_progress.dividend_income,"KZT")}/><Metric label="База" value={formatMoney(plan.target_progress.projected_final_value,"KZT")}/><Metric label="Цель" value={formatMoney(plan.target_progress.target,"KZT")}/><Metric label="Запас / дефицит" value={formatMoney(plan.target_progress.buffer_vs_target,"KZT")}/></div></CardBody></Card>
    <Card><CardHeader title="Как сделать цель устойчивее"/><CardBody><div className="grid gap-3 sm:grid-cols-3">{plan.alternative_plans.map(o=><div key={String(o.kind)} className="rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-700"><strong>{o.kind==="INCREASE_CAPITAL"?"Увеличить капитал":o.kind==="ADD_MONTHLY_CONTRIBUTION"?"Добавить пополнение":"Увеличить срок"}</strong><p className="mt-2 text-slate-500">{o.starting_capital?formatMoney(Number(o.starting_capital),"KZT"):o.monthly_contribution?`${formatMoney(Number(o.monthly_contribution),"KZT")} / мес.`:`${o.horizon_months} мес.`}</p></div>)}</div></CardBody></Card>
    <div className="sticky bottom-4 z-10 rounded-2xl border border-emerald-200 bg-white/95 p-3 shadow-xl backdrop-blur dark:border-emerald-900 dark:bg-slate-900/95"><Button className="w-full py-3" disabled={!plan.goal_id} onClick={() => void onCopy()}>Скопировать в портфель</Button>{copied&&<p className="mt-2 text-center text-xs text-emerald-600">Добавлено как PLANNED, не как покупка. <Link href="/portfolio" className="font-semibold underline">Открыть портфель</Link></p>}</div>
    <div className="space-y-1 text-xs text-slate-500">{plan.warnings.map(w=><p key={w}>• {w}</p>)}</div>
  </div>;
}

function TechnicalTiming({item}:{item:GoalPlan["initial_portfolio"][number]}) {
  if(item.instrument_type!=="stock"||!item.technical_timing)return null;
  return <div className="mt-3 rounded-xl bg-violet-50 p-3 text-xs text-violet-900 dark:bg-violet-950/30 dark:text-violet-200"><p><b>Технический timing:</b> риск {item.technical_timing.risk??"нет данных"} · импульс {item.technical_timing.momentum??"—"}/100 · надёжность {item.technical_timing.confidence??"—"}</p><p className="mt-1 text-[11px] opacity-75">Не меняет фундаментальный отбор или ожидаемую доходность.</p>{item.execution_plan?<div className="mt-2"><b>Сценарий поэтапного исполнения:</b> {item.execution_plan.tranches.map(step=>`${step.percent}% — ${step.condition}`).join("; ")}<p className="mt-1 text-[11px] opacity-75">{item.execution_plan.warning}</p></div>:null}</div>;
}

function Metric({label,value}:{label:string;value:string}) { return <div><span className="block text-xs text-slate-400">{label}</span><strong className="mt-1 block">{value}</strong></div>; }
