"use client";

import Link from "next/link";
import { use } from "react";

import { FreshnessBadge } from "@/components/layout/DataModeBanner";
import { Badge } from "@/components/ui/Badge";
import { Card, CardBody } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { BondCharts } from "@/features/bonds/BondCardSections";
import { Calculator } from "@/features/bonds/Calculator";
import { KaseVerify } from "@/features/bonds/KaseVerify";
import { PeerList } from "@/features/bonds/PeerList";
import { ProDetails } from "@/features/bonds/ProDetails";
import { ScoreExplanation } from "@/features/bonds/ScoreExplanation";
import { SimpleSummary } from "@/features/bonds/SimpleSummary";
import { WatchButton } from "@/features/bonds/WatchButton";
import { useBondCard } from "@/hooks/useBonds";
import { useUiStore } from "@/stores/uiStore";
import { bondTypeLabel } from "@/utils/score";

export default function BondPage({
  params,
}: {
  params: Promise<{ identifier: string }>;
}) {
  const { identifier } = use(params);
  const decoded = decodeURIComponent(identifier);
  const { data, isLoading, error } = useBondCard(decoded);
  const uiMode = useUiStore((s) => s.uiMode);
  const compareList = useUiStore((s) => s.compareList);
  const toggleCompare = useUiStore((s) => s.toggleCompare);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            title="Выпуск не найден"
            description="Проверьте тикер или ISIN."
            action={
              <Link href="/" className="text-sm underline">
                Вернуться к списку
              </Link>
            }
          />
        </CardBody>
      </Card>
    );
  }

  const { bond, simple, pro, freshness } = data;
  const inCompare = compareList.includes(bond.ticker);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{bond.ticker}</h1>
            <Badge>{bondTypeLabel(bond.bond_type)}</Badge>
            {bond.subordinated ? <Badge tone="warning">субординированный</Badge> : null}
            {bond.secured ? <Badge tone="success">с обеспечением</Badge> : null}
            <FreshnessBadge freshness={freshness} />
          </div>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {bond.name}
            {bond.issuer ? ` · ${bond.issuer.short_name ?? bond.issuer.name}` : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <WatchButton ticker={bond.ticker} />
          <button
            type="button"
            onClick={() => toggleCompare(bond.ticker)}
            className="rounded-xl border border-slate-200 px-3 text-sm text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"
          >
            {inCompare ? "✓ В сравнении" : "+ К сравнению"}
          </button>
        </div>
      </div>

      {data.warning ? (
        <p className="rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {data.warning}
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <SimpleSummary simple={simple} />
          <ScoreExplanation ticker={bond.ticker} />
          <BondCharts ticker={bond.ticker} currency={bond.currency} />
          {uiMode === "pro" ? <ProDetails pro={pro} bond={bond} /> : null}
        </div>

        <div className="space-y-4">
          <Calculator ticker={bond.ticker} currency={bond.currency} />
          <PeerList ticker={bond.ticker} />
          <KaseVerify ticker={bond.ticker} kaseUrl={bond.kase_url} />
        </div>
      </div>
    </div>
  );
}
