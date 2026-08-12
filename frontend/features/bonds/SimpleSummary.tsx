"use client";

import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ScoreBar, ScoreDial } from "@/components/ui/ScoreDial";
import { Stat } from "@/components/ui/Stat";
import { formatDate, formatPercent, formatYears } from "@/utils/format";
import type { SimpleView } from "@/types/api";

/** The seven ideas a non-professional needs. Nothing else appears here. */
export function SimpleSummary({ simple }: { simple: SimpleView }) {
  const realNegative =
    simple.real_yield_pct !== null && simple.real_yield_pct < 0;

  return (
    <Card>
      <CardHeader
        title="Коротко о выпуске"
        subtitle="Всё, что нужно, чтобы принять решение"
      />
      <CardBody className="space-y-6">
        <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
          <ScoreDial
            value={simple.overall.score}
            label="из 100"
            caption={simple.overall.verdict}
          />
          <div className="min-w-0 flex-1">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              {simple.overall.summary}
            </p>
            {simple.overall.confidence !== null ? (
              <p className="mt-2 text-xs text-slate-400">
                Полнота исходных данных:{" "}
                {Math.round((simple.overall.confidence ?? 0) * 100)}%
              </p>
            ) : null}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3">
          <Stat
            label="Доходность"
            value={formatPercent(simple.yield_pct)}
            hint="в год, если держать до погашения"
          />
          <Stat
            label="После инфляции"
            value={formatPercent(simple.real_yield_pct)}
            tone={realNegative ? "negative" : "positive"}
            hint={
              simple.inflation_pct === null
                ? "нет данных по инфляции"
                : `инфляция ${formatPercent(simple.inflation_pct)}`
            }
          />
          <Stat
            label="Срок"
            value={formatYears(simple.years_to_maturity)}
            hint={`до ${formatDate(simple.maturity_date)}`}
          />
        </div>

        {realNegative ? (
          <p className="rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
            Доходность ниже инфляции: покупательная способность денег снижается,
            даже если сумма на счете растет.
          </p>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-3">
          <ScoreBar
            label="Надежность"
            value={simple.reliability.score}
            hint={simple.reliability.word}
          />
          <ScoreBar
            label="Ликвидность"
            value={simple.liquidity.score}
            hint={simple.liquidity.word}
          />
          <ScoreBar
            label="Потенциал роста"
            value={simple.growth_potential.score}
            hint={simple.growth_potential.note}
          />
        </div>
      </CardBody>
    </Card>
  );
}
