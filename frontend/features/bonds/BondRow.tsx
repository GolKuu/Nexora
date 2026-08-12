"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/Badge";
import { useUiStore } from "@/stores/uiStore";
import { cn } from "@/utils/cn";
import { formatPercent, formatYears } from "@/utils/format";
import { TONE_SOFT, bondTypeLabel, scoreTone } from "@/utils/score";
import type { BondListItem } from "@/types/api";

export function BondRow({
  bond,
  rank,
  showCompare = true,
}: {
  bond: BondListItem;
  rank?: number;
  showCompare?: boolean;
}) {
  const compareList = useUiStore((s) => s.compareList);
  const toggleCompare = useUiStore((s) => s.toggleCompare);
  const inCompare = compareList.includes(bond.ticker);
  const tone = scoreTone(bond.investment_score);

  return (
    <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-3 last:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
      {rank !== undefined ? (
        <span className="tabular w-6 shrink-0 text-sm text-slate-400">{rank}</span>
      ) : null}

      <Link href={`/bond/${bond.ticker}`} className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
            {bond.ticker}
          </span>
          {bond.data_mode === "mock" ? <Badge tone="warning">демо</Badge> : null}
        </div>
        <p className="truncate text-xs text-slate-500 dark:text-slate-400">
          {bond.issuer_name ?? bond.name} · {bondTypeLabel(bond.bond_type)}
        </p>
      </Link>

      <div className="tabular hidden w-24 shrink-0 text-right sm:block">
        <div className="text-sm font-semibold">{formatPercent(bond.yield_pct)}</div>
        <div className="text-xs text-slate-500">доходность</div>
      </div>

      <div className="tabular hidden w-28 shrink-0 text-right md:block">
        <div
          className={cn(
            "text-sm font-semibold",
            (bond.real_yield_pct ?? 0) < 0
              ? "text-rose-600 dark:text-rose-400"
              : "text-emerald-600 dark:text-emerald-400",
          )}
        >
          {formatPercent(bond.real_yield_pct)}
        </div>
        <div className="text-xs text-slate-500">после инфляции</div>
      </div>

      <div className="tabular hidden w-20 shrink-0 text-right lg:block">
        <div className="text-sm">{formatYears(bond.years_to_maturity)}</div>
        <div className="text-xs text-slate-500">срок</div>
      </div>

      <div
        className={cn(
          "tabular w-14 shrink-0 rounded-lg py-1.5 text-center text-sm font-semibold",
          TONE_SOFT[tone],
        )}
        title="Общая оценка"
      >
        {bond.investment_score === null ? "—" : Math.round(bond.investment_score)}
      </div>

      {showCompare ? (
        <button
          type="button"
          onClick={() => toggleCompare(bond.ticker)}
          aria-pressed={inCompare}
          title={inCompare ? "Убрать из сравнения" : "Добавить к сравнению"}
          className={cn(
            "hidden h-8 w-8 shrink-0 items-center justify-center rounded-lg border text-sm sm:flex",
            inCompare
              ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900"
              : "border-slate-200 text-slate-400 hover:text-slate-700 dark:border-slate-700",
          )}
        >
          {inCompare ? "✓" : "+"}
        </button>
      ) : null}
    </div>
  );
}
