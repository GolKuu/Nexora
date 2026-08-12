"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Field";
import { Stat } from "@/components/ui/Stat";
import { bondsService } from "@/services/bonds";
import { useUiStore } from "@/stores/uiStore";
import { formatMoney, formatPercent, formatYears } from "@/utils/format";
import type { CalculatorResult } from "@/types/api";

const PRESETS = [100_000, 500_000, 1_000_000, 5_000_000];

/** «Если вложить X ₸». Every figure is computed by the backend. */
export function Calculator({ ticker, currency }: { ticker: string; currency: string }) {
  const amount = useUiStore((s) => s.calculatorAmount);
  const setAmount = useUiStore((s) => s.setCalculatorAmount);
  const [result, setResult] = useState<CalculatorResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(value: number) {
    setLoading(true);
    setError(null);
    try {
      setResult(await bondsService.calculate(ticker, value));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось посчитать");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Если вложить"
        subtitle="Расчет по текущей цене и графику выплат выпуска"
      />
      <CardBody className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {PRESETS.map((preset) => (
            <button
              key={preset}
              type="button"
              onClick={() => {
                setAmount(preset);
                void run(preset);
              }}
              className="rounded-lg bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
            >
              {formatMoney(preset, currency)}
            </button>
          ))}
        </div>

        <div className="flex gap-2">
          <Input
            type="number"
            min={0}
            step={10_000}
            value={amount}
            onChange={(e) => setAmount(Number(e.target.value))}
            aria-label="Сумма вложения"
          />
          <Button onClick={() => void run(amount)} disabled={loading || amount <= 0}>
            {loading ? "Считаем…" : "Посчитать"}
          </Button>
        </div>

        {error ? <p className="text-sm text-rose-600">{error}</p> : null}

        {result && !result.available ? (
          <p className="rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {result.reason}
          </p>
        ) : null}

        {result?.available ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Stat
                label="Купите"
                value={`${result.quantity} шт`}
                hint={`по ${formatMoney(result.price_per_bond, result.currency, 2)}`}
              />
              <Stat
                label="Вложите"
                value={formatMoney(result.invested, result.currency)}
                hint={`остаток ${formatMoney(result.uninvested_remainder, result.currency)}`}
              />
              <Stat
                label="Получите всего"
                value={formatMoney(result.proceeds, result.currency)}
                hint={`за ${formatYears(result.years)}`}
              />
              <Stat
                label="Прибыль"
                value={formatMoney(result.profit, result.currency)}
                tone={(result.profit ?? 0) >= 0 ? "positive" : "negative"}
                hint={`${formatPercent(result.annualized_return_pct)} в год`}
              />
            </div>

            <div className="rounded-xl bg-slate-50 p-4 dark:bg-slate-800">
              <p className="text-sm font-medium text-slate-700 dark:text-slate-200">
                С учетом инфляции {formatPercent(result.inflation_pct)}
              </p>
              <div className="mt-2 grid grid-cols-2 gap-4">
                <Stat
                  label="Реальная прибыль"
                  value={formatMoney(result.profit_real, result.currency)}
                  tone={(result.profit_real ?? 0) >= 0 ? "positive" : "negative"}
                  hint="в сегодняшних деньгах"
                />
                <Stat
                  label="Реальная доходность"
                  value={formatPercent(result.real_annualized_return_pct)}
                  tone={
                    (result.real_annualized_return_pct ?? 0) >= 0 ? "positive" : "negative"
                  }
                  hint="в год после инфляции"
                />
              </div>
            </div>

            <ul className="space-y-0.5 text-xs text-slate-500 dark:text-slate-400">
              {result.assumptions?.map((line) => (
                <li key={line}>• {line}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}
