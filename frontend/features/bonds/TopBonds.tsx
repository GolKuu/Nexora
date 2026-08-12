"use client";

import { useState } from "react";

import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { BondRow } from "@/features/bonds/BondRow";
import { useTopBonds } from "@/hooks/useBonds";
import { cn } from "@/utils/cn";

const CATEGORIES = [
  { code: undefined, label: "Все" },
  { code: "government", label: "Государственные" },
  { code: "quasi_sovereign", label: "Квазигос" },
  { code: "bank", label: "Банки" },
  { code: "corporate", label: "Компании" },
] as const;

export function TopBonds({ limit = 10 }: { limit?: number }) {
  const [category, setCategory] = useState<string | undefined>(undefined);
  const { data, isLoading, error } = useTopBonds(limit, category);

  return (
    <Card>
      <CardHeader
        title="TOP облигаций"
        subtitle="Отсортировано по общей оценке. Считает движок, не модель ИИ."
      />

      <div className="flex gap-1.5 overflow-x-auto border-b border-slate-100 px-4 py-2.5 dark:border-slate-800">
        {CATEGORIES.map((item) => (
          <button
            key={item.label}
            type="button"
            onClick={() => setCategory(item.code)}
            className={cn(
              "shrink-0 rounded-lg px-3 py-1 text-xs font-medium transition-colors",
              category === item.code
                ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="space-y-2 p-4">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-12 w-full" />
          ))}
        </div>
      ) : error ? (
        <EmptyState
          title="Не удалось загрузить список"
          description="Проверьте, что бэкенд запущен и данные загружены."
        />
      ) : !data?.items.length ? (
        <EmptyState
          title="Пока нет оцененных выпусков"
          description="Загрузите данные: python scripts/seed_demo.py"
        />
      ) : (
        <div>
          {data.items.map((bond, index) => (
            <BondRow key={bond.id} bond={bond} rank={index + 1} />
          ))}
        </div>
      )}
    </Card>
  );
}
