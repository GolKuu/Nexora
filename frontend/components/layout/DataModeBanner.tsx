"use client";

import { Badge } from "@/components/ui/Badge";
import { useKaseHealth } from "@/hooks/useBonds";
import { formatAge } from "@/utils/format";
import type { Freshness } from "@/types/api";

/** Site-wide honesty banner.
 *
 *  If the backend is serving demo data, the user is told on every page - it is
 *  never presented as market data. */
export function DataModeBanner() {
  const { data } = useKaseHealth();
  if (!data) return null;

  if (data.is_mock) {
    return (
      <div className="border-b border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950">
        <div className="mx-auto max-w-6xl px-4 py-2 text-sm text-amber-900 dark:text-amber-200">
          <strong className="font-semibold">Демо-режим.</strong>{" "}
          KASE не подключен — все цены, доходности и отчетность синтетические и
          нужны только для демонстрации интерфейса.
        </div>
      </div>
    );
  }

  if (!data.connected) {
    return (
      <div className="border-b border-rose-200 bg-rose-50 dark:border-rose-900 dark:bg-rose-950">
        <div className="mx-auto max-w-6xl px-4 py-2 text-sm text-rose-900 dark:text-rose-200">
          Источник данных KASE сейчас недоступен: {data.detail ?? "нет ответа"}.
          Показаны последние сохраненные значения.
        </div>
      </div>
    );
  }

  return null;
}

export function FreshnessBadge({ freshness }: { freshness: Freshness }) {
  if (freshness.is_mock) {
    return <Badge tone="warning">Демо-данные</Badge>;
  }
  const stale = (freshness.age_hours ?? 0) > 72;
  return (
    <Badge tone={stale ? "warning" : "info"}>
      {freshness.label ?? "Данные"} · {formatAge(freshness.age_hours)}
    </Badge>
  );
}
