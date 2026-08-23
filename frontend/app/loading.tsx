export default function Loading() {
  return <main className="mx-auto max-w-7xl space-y-5 px-4 py-8" aria-busy="true" aria-label="Загрузка"><div className="h-8 w-64 animate-pulse rounded-xl bg-slate-200 dark:bg-slate-800" /><div className="grid gap-4 md:grid-cols-3">{Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-36 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-900" />)}</div></main>;
}
