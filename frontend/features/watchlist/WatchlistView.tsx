"use client";

import useSWR from "swr";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { BondRow } from "@/features/bonds/BondRow";
import { watchlistService } from "@/services/user";
import Link from "next/link";
import { formatMoney } from "@/utils/format";

export function WatchlistView() {
  const { data, isLoading } = useSWR("watchlist", () => watchlistService.list(), {
    revalidateOnFocus: false,
  });

  return (
    <Card>
      <CardHeader
        title="Избранное"
        subtitle="Сохраняется в этом браузере. Регистрация нужна только для синхронизации между устройствами."
      />
      {isLoading ? (
        <CardBody>
          <Skeleton className="h-24 w-full" />
        </CardBody>
      ) : !data?.items.length ? (
        <CardBody>
          <EmptyState
            title="Список пуст"
            description="Откройте карточку облигации или акции и нажмите «В избранное»."
          />
        </CardBody>
      ) : (
        <div>
          {data.items.map((item) => item.instrument_type === "bond" ? (
            <BondRow key={`bond-${item.id}`} bond={item} />
          ) : (
            <Link key={`stock-${item.id}`} href={`/stock/${item.ticker}`} className="grid grid-cols-[1fr_auto] items-center gap-3 border-b border-slate-100 px-4 py-3 last:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
              <span className="min-w-0"><span className="block font-semibold">{item.ticker}</span><span className="block truncate text-xs text-slate-500">{item.company_name} · {item.type_label}</span></span>
              <span className="text-right"><span className="block font-semibold tabular">{formatMoney(item.price, item.currency, 2)}</span><span className="block text-xs text-emerald-600">{item.scores.investment?.value == null ? "нет оценки" : `${Math.round(item.scores.investment.value)}/100`}</span></span>
            </Link>
          ))}
        </div>
      )}
    </Card>
  );
}
