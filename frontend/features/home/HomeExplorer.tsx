"use client";

import { useState } from "react";
import { TopBonds } from "@/features/bonds/TopBonds";
import { TopStocks } from "@/features/stocks/TopStocks";
import { cn } from "@/utils/cn";

type Asset = "bonds" | "stocks" | "all";
export function HomeExplorer() {
  const [asset, setAsset] = useState<Asset>("all");
  const [amount, setAmount] = useState("5000000");
  const [profile, setProfile] = useState("balanced");
  return <div className="space-y-5">
    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-sm font-semibold">Что хотите анализировать?</h2>
      <div className="mt-3 grid grid-cols-3 gap-2">{[["bonds", "Облигации"], ["stocks", "Акции"], ["all", "Все инструменты"]].map(([code, label]) => <button key={code} onClick={() => setAsset(code as Asset)} className={cn("rounded-xl border px-3 py-2 text-sm", asset === code ? "border-emerald-500 bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200" : "border-slate-200 dark:border-slate-700")}>{label}</button>)}</div>
      {asset !== "bonds" ? <div className="mt-4 grid gap-4 sm:grid-cols-2"><label className="text-xs text-slate-500">Сколько хотите вложить?<input value={amount} onChange={(e) => setAmount(e.target.value.replace(/\D/g, ""))} inputMode="numeric" className="mt-1 block h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-base text-slate-900 dark:border-slate-700 dark:text-slate-100" /></label><div><span className="text-xs text-slate-500">Профиль</span><div className="mt-1 flex gap-1 overflow-x-auto">{[["balanced", "Баланс"], ["growth", "Рост"], ["dividend", "Дивиденды"], ["conservative", "Осторожно"]].map(([code, label]) => <button key={code} onClick={() => setProfile(code)} className={cn("rounded-lg px-2.5 py-2 text-xs", profile === code ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 dark:bg-slate-800")}>{label}</button>)}</div></div></div> : null}
      <p className="mt-3 text-xs text-slate-400">Сумма и профиль применяются в калькуляторе и рекомендациях; будущая цена всегда показывается только как сценарий.</p>
    </section>
    {(asset === "stocks" || asset === "all") && <TopStocks limit={asset === "all" ? 6 : 12} />}
    {(asset === "bonds" || asset === "all") && <TopBonds limit={asset === "all" ? 6 : 12} />}
  </div>;
}
