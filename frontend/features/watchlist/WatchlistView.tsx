"use client";

import useSWR from "swr";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { BondRow } from "@/features/bonds/BondRow";
import { watchlistService } from "@/services/user";

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
            description="Откройте карточку облигации и нажмите «В избранное»."
          />
        </CardBody>
      ) : (
        <div>
          {data.items.map((bond) => (
            <BondRow key={bond.id} bond={bond} />
          ))}
        </div>
      )}
    </Card>
  );
}
