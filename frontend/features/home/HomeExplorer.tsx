"use client";

import { useState } from "react";
import { TopBonds } from "@/features/bonds/TopBonds";
import { HomeRecommendations } from "@/features/home/HomeRecommendations";
import { TopStocks } from "@/features/stocks/TopStocks";
import { cn } from "@/utils/cn";

type Asset = "bonds" | "stocks" | "all";
type Profile = "conservative" | "balanced" | "growth" | "dividend";

const ASSETS: Array<[Asset, string]> = [["bonds", "Облигации"], ["stocks", "Акции"], ["all", "Все инструменты"]];
const PROFILES: Array<[Profile, string]> = [["balanced", "Баланс"], ["growth", "Рост"], ["dividend", "Дивиденды"], ["conservative", "Осторожный"]];

export function HomeExplorer() {
  const [asset, setAsset] = useState<Asset>("all");
  const [amount, setAmount] = useState("5000000");
  const [profile, setProfile] = useState<Profile>("balanced");
  const numericAmount = Number(amount) || 0;

  return <div className="space-y-5">
    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-sm font-semibold">Что хотите анализировать?</h2>
      <div className="mt-3 grid grid-cols-3 gap-2">{ASSETS.map(([code, label]) => <button key={code} type="button" onClick={() => setAsset(code)} className={cn("rounded-xl border px-3 py-2 text-sm", asset === code ? "border-emerald-500 bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200" : "border-slate-200 dark:border-slate-700")}>{label}</button>)}</div>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="text-xs text-slate-500">Сколько хотите вложить?<input value={amount} onChange={(event) => setAmount(event.target.value.replace(/\D/g, ""))} inputMode="numeric" className="mt-1 block h-11 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-base text-slate-900 dark:border-slate-700 dark:text-slate-100" /></label>
        <div><span className="text-xs text-slate-500">Профиль</span><div className="mt-1 flex gap-1 overflow-x-auto">{PROFILES.map(([code, label]) => <button key={code} type="button" onClick={() => setProfile(code)} className={cn("rounded-lg px-2.5 py-2 text-xs", profile === code ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "bg-slate-100 dark:bg-slate-800")}>{label}</button>)}</div></div>
      </div>
      <p className="mt-3 text-xs text-slate-400">Сумма и профиль сразу пересчитывают персональный подбор; будущая цена показывается только как сценарий.</p>
    </section>
    {numericAmount > 0 ? <HomeRecommendations asset={asset} amount={numericAmount} profile={profile} /> : <p className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">Введите сумму больше нуля для персонального подбора.</p>}
    {(asset === "stocks" || asset === "all") && <TopStocks limit={asset === "all" ? 6 : 12} />}
    {(asset === "bonds" || asset === "all") && <TopBonds limit={asset === "all" ? 6 : 12} />}
  </div>;
}
