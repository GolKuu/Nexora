"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";

export function DCFEntry() {
  const [ticker, setTicker] = useState("");
  const router = useRouter();

  function submit(event: FormEvent) {
    event.preventDefault();
    const identifier = ticker.trim();
    if (identifier) router.push(`/stock/${encodeURIComponent(identifier)}`);
  }

  return <Card className="border-sky-200 bg-gradient-to-r from-sky-50 to-white dark:border-sky-900 dark:from-sky-950/40 dark:to-slate-900">
    <CardBody className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-sky-700 dark:text-sky-300">AI DCF</p>
        <h2 className="mt-1 text-xl font-semibold">Оцените справедливую стоимость акции</h2>
        <p className="mt-1 text-sm text-slate-500">Bear / Base / Bull по опубликованной отчётности и детерминированной модели.</p>
        <form onSubmit={submit} className="mt-4 flex gap-2">
          <input value={ticker} onChange={event => setTicker(event.target.value.toUpperCase())} placeholder="Введите тикер, например KZAP" className="h-11 min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3 dark:border-slate-700 dark:bg-slate-900" />
          <Button type="submit" disabled={!ticker.trim()}>Проанализировать</Button>
        </form>
      </div>
      <div className="rounded-xl bg-white/80 px-4 py-3 text-sm dark:bg-slate-900/70">
        <p className="text-xs text-slate-500">Доступ</p>
        <p className="font-semibold">Бесплатно</p>
        <p className="mt-1 text-xs text-slate-500">Без подписки и без лимита расчётов</p>
      </div>
    </CardBody>
  </Card>;
}
