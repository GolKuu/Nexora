"use client";

import { useEffect } from "react";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { console.error(error); }, [error]);
  return <main className="mx-auto flex min-h-[60vh] max-w-xl items-center px-4 py-16"><section className="w-full rounded-3xl border border-rose-200 bg-white p-8 text-center shadow-sm dark:border-rose-900 dark:bg-slate-900"><p className="text-xs font-semibold uppercase tracking-widest text-rose-600">Ошибка загрузки</p><h1 className="mt-3 text-2xl font-bold">Экран временно недоступен</h1><p className="mt-2 text-sm text-slate-500">Данные не потеряны. Попробуйте загрузить этот раздел ещё раз.</p><button type="button" onClick={reset} className="mt-6 rounded-xl bg-slate-900 px-5 py-3 text-sm font-semibold text-white dark:bg-white dark:text-slate-900">Повторить</button></section></main>;
}
