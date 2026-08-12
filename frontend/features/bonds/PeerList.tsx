"use client";

import Link from "next/link";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Stat";
import { usePeers } from "@/hooks/useBonds";
import { formatRate, formatYears } from "@/utils/format";

export function PeerList({ ticker }: { ticker: string }) {
  const { data } = usePeers(ticker);
  if (!data) return null;

  return (
    <Card>
      <CardHeader
        title="Похожие выпуски"
        subtitle={
          data.peer_group
            ? `Группа сравнения: ${data.peer_group}`
            : "Группа сравнения не определена"
        }
      />
      <CardBody className="py-1">
        {data.peers.length === 0 ? (
          <EmptyState
            title="Похожих выпусков нет"
            description="Сравнение с рынком по этому выпуску пока невозможно."
          />
        ) : (
          data.peers.map((peer) => (
            <Link
              key={peer.id}
              href={`/bond/${peer.ticker}`}
              className="flex items-center justify-between gap-3 border-b border-slate-100 py-2 last:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
            >
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{peer.ticker}</span>
                <span className="block truncate text-xs text-slate-500">{peer.name}</span>
              </span>
              <span className="tabular shrink-0 text-right text-sm">
                <span className="block font-semibold">{formatRate(peer.ytm)}</span>
                <span className="block text-xs text-slate-500">
                  {formatYears(peer.years_to_maturity)}
                </span>
              </span>
            </Link>
          ))
        )}
      </CardBody>
    </Card>
  );
}
