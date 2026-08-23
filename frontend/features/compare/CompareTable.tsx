"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { bondsService } from "@/services/bonds";
import { useUiStore } from "@/stores/uiStore";
import { cn } from "@/utils/cn";
import { formatMoney, formatNumber } from "@/utils/format";
import type { CompareResponse } from "@/types/api";

function renderValue(key: string, value: number | null, unit: string, currency: string): string {
  if (value === null || value === undefined) return "—";
  if (unit === "%") return `${formatNumber(value, 2)}%`;
  if (unit === "доля") return `${formatNumber(value * 100, 2)}%`;
  if (unit === "0-100") return String(Math.round(value));
  if (unit === "money") return formatMoney(value, currency, 2);
  if (unit === "лет") return formatNumber(value, 2);
  return formatNumber(value, 3);
}

export function CompareTable() {
  const compareList = useUiStore((s) => s.compareList);
  const clearCompare = useUiStore((s) => s.clearCompare);
  const toggleCompare = useUiStore((s) => s.toggleCompare);
  const uiMode = useUiStore((s) => s.uiMode);
  const [amount, setAmount] = useState("1000000");

  const { data, isLoading, error } = useSWR<CompareResponse>(
    compareList.length ? ["compare", compareList.join(","), uiMode, amount] : null,
    () => bondsService.compare(compareList, uiMode, Number(amount) || undefined),
    { revalidateOnFocus: false },
  );

  if (compareList.length === 0) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            title="Выберите выпуски для сравнения"
            description="Отметьте кнопкой «+» до пяти облигаций в списке TOP или в поиске."
            action={
              <Link href="/">
                <Button variant="secondary" size="sm">
                  Перейти к списку
                </Button>
              </Link>
            }
          />
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Сравнение"
        subtitle={`${compareList.length} из 5 · режим «${uiMode === "simple" ? "Просто" : "Подробно"}»`}
        action={
          <Button variant="ghost" size="sm" onClick={clearCompare}>
            Очистить
          </Button>
        }
      />
      <CardBody className="overflow-x-auto">
        <label className="mb-4 block max-w-xs text-xs text-slate-500">Одинаковая сумма на каждый выпуск<input value={amount} onChange={(event) => setAmount(event.target.value.replace(/\D/g, ""))} inputMode="numeric" className="mt-1 h-10 w-full rounded-xl border border-slate-200 bg-transparent px-3 text-sm dark:border-slate-700" /></label>
        {isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : error || !data ? (
          <EmptyState title="Не удалось загрузить сравнение" />
        ) : (
          <>
            {data.winner ? (
              <p className="mb-4 rounded-xl bg-emerald-50 px-3 py-2 text-sm text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200">
                Лучшая общая оценка:{" "}
                <strong>{data.winner.ticker}</strong> ({Math.round(data.winner.investment_score)}
                /100). {data.winner.reason}
              </p>
            ) : null}

            <table className="w-full min-w-[560px] border-collapse text-sm">
              <thead>
                <tr>
                  <th className="w-44 py-2 text-left font-medium text-slate-400">
                    Показатель
                  </th>
                  {data.columns.map((column) => (
                    <th key={column.id} className="px-2 py-2 text-left">
                      <Link
                        href={`/bond/${column.ticker}`}
                        className="font-semibold hover:underline"
                      >
                        {column.ticker}
                      </Link>
                      <p className="truncate text-xs font-normal text-slate-500">
                        {column.issuer ?? column.name}
                      </p>
                      <div className="mt-1 flex items-center gap-1">
                        {column.data_mode === "mock" ? (
                          <Badge tone="warning">демо</Badge>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => toggleCompare(column.ticker)}
                          className="text-xs text-slate-400 hover:text-rose-600"
                        >
                          убрать
                        </button>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.key} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-2 pr-2 text-slate-500 dark:text-slate-400">
                      {row.label}
                    </td>
                    {data.columns.map((column) => {
                      const isBest = data.best[row.key] === column.id;
                      return (
                        <td
                          key={column.id}
                          className={cn(
                            "tabular px-2 py-2",
                            isBest &&
                              "font-semibold text-emerald-700 dark:text-emerald-400",
                          )}
                        >
                          {renderValue(row.key, column.values[row.key] ?? null, row.unit, column.currency)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </CardBody>
    </Card>
  );
}
