import { TopBonds } from "@/features/bonds/TopBonds";
import { SearchBar } from "@/features/search/SearchBar";

export default function HomePage() {
  return (
    <div className="space-y-6">
      <section className="pt-4 text-center sm:pt-10">
        <h1 className="text-3xl font-semibold tracking-tight text-slate-900 dark:text-slate-50 sm:text-4xl">
          KASE Bond AI
        </h1>
        <p className="mx-auto mt-2 max-w-xl text-sm text-slate-500 dark:text-slate-400">
          Понятный анализ облигаций Казахстанской фондовой биржи: сколько вы
          заработаете, что останется после инфляции и насколько это надежно.
        </p>
        <div className="mx-auto mt-6 max-w-2xl">
          <SearchBar />
        </div>
        <p className="mt-3 text-xs text-slate-400">
          Регистрация не нужна: поиск, TOP, карточки и калькулятор доступны сразу.
        </p>
      </section>

      <TopBonds limit={12} />
    </div>
  );
}
