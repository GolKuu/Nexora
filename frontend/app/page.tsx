import { HomeExplorer } from "@/features/home/HomeExplorer";
import { HomePulse } from "@/features/home/HomePulse";
import { SearchBar } from "@/features/search/SearchBar";

export default function HomePage() {
  return (
    <div className="space-y-6">
      <section className="pt-4 text-center sm:pt-10">
        <h1 className="text-3xl font-semibold tracking-tight text-slate-900 dark:text-slate-50 sm:text-4xl">
          KASE Investment AI
        </h1>
        <p className="mx-auto mt-2 max-w-xl text-sm text-slate-500 dark:text-slate-400">
          Акции и облигации KASE в одном продукте — с разными моделями анализа,
          проверяемыми источниками и прозрачными сценариями.
        </p>
        <div className="mx-auto mt-6 max-w-2xl">
          <SearchBar />
        </div>
        <p className="mt-3 text-xs text-slate-400">
          Регистрация не нужна: поиск, TOP, карточки и калькулятор доступны сразу.
        </p>
      </section>

      <HomeExplorer />
      <HomePulse />
    </div>
  );
}
