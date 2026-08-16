"use client";

import Link from "next/link";
import { Fragment, useState } from "react";
import useSWR, { mutate } from "swr";

import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { EmptyState, Skeleton, Stat } from "@/components/ui/Stat";
import { portfolioService } from "@/services/user";
import { useUiStore } from "@/stores/uiStore";
import { formatMoney, formatNumber, formatPercent, formatRate } from "@/utils/format";

function AddPositionForm({ portfolioId }: { portfolioId: number }) {
  const [instrumentType, setInstrumentType] = useState<"bond" | "stock">("bond");
  const [identifier, setIdentifier] = useState("");
  const [quantity, setQuantity] = useState(100);
  const [price, setPrice] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await portfolioService.addPosition(portfolioId, {
        [instrumentType]: identifier.trim(),
        instrument_type: instrumentType,
        quantity,
        ...(instrumentType === "bond" ? { purchase_clean_price: price ? Number(price) : undefined } : { purchase_price: price ? Number(price) : undefined }),
      });
      setIdentifier("");
      await mutate(["portfolio", portfolioId]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось добавить позицию");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-3 sm:grid-cols-5">
      <Field label="Тип инструмента">
        <select value={instrumentType} onChange={(event) => setInstrumentType(event.target.value as "bond" | "stock")} className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900">
          <option value="bond">Облигация</option><option value="stock">Акция</option>
        </select>
      </Field>
      <Field label="Тикер или ISIN">
        <Input value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder={instrumentType === "bond" ? "DBNKb1" : "HSBK"} />
      </Field>
      <Field label="Количество">
        <Input
          type="number"
          min={1}
          value={quantity}
          onChange={(e) => setQuantity(Number(e.target.value))}
        />
      </Field>
      <Field label={instrumentType === "bond" ? "Цена, % номинала" : "Цена за акцию"} hint="можно не указывать">
        <Input
          type="number"
          step="0.01"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          placeholder="98.5"
        />
      </Field>
      <div className="flex items-end">
        <Button onClick={() => void submit()} disabled={busy || !identifier.trim()} className="w-full">
          Добавить
        </Button>
      </div>
      {error ? <p className="text-sm text-rose-600 sm:col-span-5">{error}</p> : null}
    </div>
  );
}

function PortfolioDetailView({ portfolioId }: { portfolioId: number }) {
  const uiMode = useUiStore((s) => s.uiMode);
  const { data, isLoading } = useSWR(["portfolio", portfolioId], () =>
    portfolioService.detail(portfolioId),
  );

  if (isLoading || !data) return <Skeleton className="h-48 w-full" />;

  const currency = data.base_currency;
  const summary = data.summary;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title={data.name} subtitle={`${summary.position_count} позиций`} />
        <CardBody>
          <div className="grid grid-cols-2 gap-5 sm:grid-cols-4">
            <Stat
              label="Стоимость"
              value={formatMoney(summary.market_value, currency)}
              hint={summary.cost ? `вложено ${formatMoney(summary.cost, currency)}` : undefined}
            />
            <Stat
              label="Результат"
              value={formatMoney(summary.unrealized_pnl, currency)}
              tone={(summary.unrealized_pnl ?? 0) >= 0 ? "positive" : "negative"}
            />
            <Stat
              label="Доходность"
              value={formatPercent(summary.portfolio_ytm_pct)}
              hint="средневзвешенная"
            />
            <Stat
              label="После инфляции"
              value={formatPercent(summary.portfolio_real_ytm_pct)}
              tone={(summary.portfolio_real_ytm_pct ?? 0) >= 0 ? "positive" : "negative"}
              hint={
                summary.inflation_pct === null
                  ? undefined
                  : `инфляция ${formatPercent(summary.inflation_pct)}`
              }
            />
          </div>
          <div className="mt-4 grid grid-cols-2 gap-5 border-t border-slate-100 pt-4 dark:border-slate-800 sm:grid-cols-4">
            <Stat label="Акции" value={formatMoney(summary.asset_allocation.stocks, currency)} />
            <Stat label="Облигации" value={formatMoney(summary.asset_allocation.bonds, currency)} />
            <Stat label="Дивиденды (trailing)" value={formatMoney(summary.dividends, currency)} />
            <Stat label="Купоны" value={summary.coupons == null ? "—" : formatMoney(summary.coupons, currency)} />
          </div>
          <div className="mt-4 grid gap-4 border-t border-slate-100 pt-4 dark:border-slate-800 sm:grid-cols-2">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Валютная структура</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.entries(summary.currency_allocation).map(([code, value]) => (
                  <span key={code} className="rounded-full bg-slate-100 px-3 py-1 text-sm dark:bg-slate-800">
                    {code}: {formatMoney(value, code)}
                  </span>
                ))}
              </div>
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Концентрация по эмитентам</p>
              <div className="mt-2 space-y-1 text-sm">
                {summary.issuer_concentration.slice(0, 5).map((issuer) => (
                  <div key={issuer.issuer_id} className="flex justify-between gap-3">
                    <span className="truncate">{issuer.issuer_name}</span>
                    <span className="shrink-0 tabular-nums">
                      {issuer.percent == null
                        ? formatMoney(issuer.market_value, currency)
                        : `${formatNumber(issuer.percent, 1)}%`}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
          {uiMode === "pro" ? (
            <div className="mt-4 grid grid-cols-2 gap-5 border-t border-slate-100 pt-4 dark:border-slate-800 sm:grid-cols-4">
              <Stat
                label="Duration"
                value={formatNumber(summary.portfolio_duration, 2)}
                hint="модифицированная, взвешенная"
              />
              <Stat
                label="Средняя оценка"
                value={
                  summary.average_investment_score === null
                    ? "—"
                    : String(summary.average_investment_score)
                }
              />
            </div>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Позиции" />
        <CardBody className="overflow-x-auto">
          {data.positions.length === 0 ? (
            <EmptyState title="Позиций пока нет" />
          ) : (
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-slate-400">
                  <th className="py-2">Выпуск</th>
                  <th className="py-2 text-right">Кол-во</th>
                  <th className="py-2 text-right">Цена</th>
                  <th className="py-2 text-right">Стоимость</th>
                  <th className="py-2 text-right">Результат</th>
                  <th className="py-2 text-right">Доходность</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {["stock", "bond"].map((kind) => {
                  const group = data.positions.filter((position) => position.instrument_type === kind);
                  if (!group.length) return null;
                  return <Fragment key={kind}><tr className="border-t border-slate-200 bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:bg-slate-800"><td colSpan={7} className="px-2 py-2">{kind === "stock" ? "Акции" : "Облигации"}</td></tr>{group.map((position) => (
                  <tr
                    key={position.id}
                    className="border-t border-slate-100 dark:border-slate-800"
                  >
                    <td className="py-2">
                      <Link href={position.instrument_type === "stock" ? `/stock/${position.ticker}` : `/bond/${position.ticker}`} className="font-medium hover:underline">
                        {position.ticker}
                      </Link>
                      <p className="truncate text-xs text-slate-500">{position.name}</p>
                    </td>
                    <td className="tabular py-2 text-right">{position.quantity}</td>
                    <td className="tabular py-2 text-right">
                      {formatNumber(position.instrument_type === "stock" ? position.current_price : position.clean_price, 2)}
                    </td>
                    <td className="tabular py-2 text-right">
                      {formatMoney(position.market_value, position.currency)}
                    </td>
                    <td
                      className={`tabular py-2 text-right ${
                        (position.unrealized_pnl ?? 0) >= 0
                          ? "text-emerald-600"
                          : "text-rose-600"
                      }`}
                    >
                      {formatMoney(position.unrealized_pnl, position.currency)}
                    </td>
                    <td className="tabular py-2 text-right">{position.instrument_type === "stock" ? formatMoney(position.dividend_income_trailing, position.currency) : formatRate(position.ytm)}</td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        className="text-xs text-slate-400 hover:text-rose-600"
                        onClick={async () => {
                          await portfolioService.removePosition(portfolioId, position.id);
                          await mutate(["portfolio", portfolioId]);
                        }}
                      >
                        удалить
                      </button>
                    </td>
                  </tr>))}</Fragment>;
                })}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Добавить позицию" />
        <CardBody>
          <AddPositionForm portfolioId={portfolioId} />
        </CardBody>
      </Card>
    </div>
  );
}

export function PortfolioView() {
  const { data, isLoading } = useSWR("portfolios", () => portfolioService.list());
  const [creating, setCreating] = useState(false);

  if (isLoading) return <Skeleton className="h-48 w-full" />;

  if (!data?.items.length) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            title="У вас пока нет портфеля"
            description="Портфель хранится в этом браузере. Регистрация нужна только для синхронизации между устройствами и алертов."
            action={
              <Button
                disabled={creating}
                onClick={async () => {
                  setCreating(true);
                  try {
                    await portfolioService.create("Мой портфель");
                    await mutate("portfolios");
                  } finally {
                    setCreating(false);
                  }
                }}
              >
                Создать портфель
              </Button>
            }
          />
        </CardBody>
      </Card>
    );
  }

  return <PortfolioDetailView portfolioId={data.items[0].id} />;
}
