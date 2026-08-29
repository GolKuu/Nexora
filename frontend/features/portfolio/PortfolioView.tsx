"use client";

import Link from "next/link";
import { Fragment, useState } from "react";
import useSWR, { mutate } from "swr";

import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { EmptyState, Skeleton, Stat } from "@/components/ui/Stat";
import { goalPlannerService, portfolioService } from "@/services/user";
import { useUiStore } from "@/stores/uiStore";
import { formatDate, formatMoney, formatNumber, formatPercent, formatRate } from "@/utils/format";
import type { PortfolioDetail, PortfolioPosition } from "@/types/api";

function AddPositionForm({ portfolioId }: { portfolioId: number }) {
  const [instrumentType, setInstrumentType] = useState<"bond" | "stock">("bond");
  const [identifier, setIdentifier] = useState("");
  const [quantity, setQuantity] = useState(100);
  const [price, setPrice] = useState<string>("");
  const [purchaseDate, setPurchaseDate] = useState("");
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
        purchase_date: purchaseDate || undefined,
        ...(instrumentType === "bond" ? { purchase_clean_price: price ? Number(price) : undefined } : { purchase_price: price ? Number(price) : undefined }),
      });
      setIdentifier("");
      setPurchaseDate("");
      await mutate(["portfolio", portfolioId]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось добавить позицию");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-3 sm:grid-cols-6">
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
      <Field label="Дата покупки" hint="для корректного дохода">
        <Input type="date" value={purchaseDate} onChange={(e) => setPurchaseDate(e.target.value)} />
      </Field>
      <div className="flex items-end">
        <Button onClick={() => void submit()} disabled={busy || !identifier.trim()} className="w-full">
          Добавить
        </Button>
      </div>
      {error ? <p className="text-sm text-rose-600 sm:col-span-6">{error}</p> : null}
    </div>
  );
}

function EditPositionForm({ portfolioId, position, onClose }: { portfolioId: number; position: PortfolioPosition; onClose: () => void }) {
  const [quantity, setQuantity] = useState(String(position.quantity));
  const [price, setPrice] = useState(String(position.instrument_type === "stock" ? position.purchase_price ?? "" : position.purchase_clean_price ?? ""));
  const [purchaseDate, setPurchaseDate] = useState(position.purchase_date ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true); setError(null);
    try {
      await portfolioService.updatePosition(portfolioId, position.id, {
        quantity: Number(quantity),
        purchase_date: purchaseDate || undefined,
        ...(position.instrument_type === "stock"
          ? { purchase_price: price ? Number(price) : undefined }
          : { purchase_clean_price: price ? Number(price) : undefined }),
      });
      await mutate(["portfolio", portfolioId]);
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось изменить позицию");
    } finally { setBusy(false); }
  }

  return <div className="mt-3 grid gap-3 rounded-xl border border-slate-200 p-3 sm:grid-cols-[1fr_1fr_1fr_auto] dark:border-slate-700">
    <Field label="Количество"><Input type="number" min="0.000001" step="any" value={quantity} onChange={event => setQuantity(event.target.value)} /></Field>
    <Field label={position.instrument_type === "stock" ? "Цена покупки" : "Цена, % номинала"}><Input type="number" min="0.000001" step="any" value={price} onChange={event => setPrice(event.target.value)} /></Field>
    <Field label="Дата покупки"><Input type="date" value={purchaseDate} onChange={event => setPurchaseDate(event.target.value)} /></Field>
    <div className="flex items-end gap-2"><Button onClick={() => void save()} disabled={busy || Number(quantity) <= 0}>Сохранить</Button><button type="button" className="px-2 py-2 text-xs text-slate-500" onClick={onClose}>Отмена</button></div>
    {error ? <p className="text-sm text-rose-600 sm:col-span-4">{error}</p> : null}
  </div>;
}

function PortfolioDetailView({ portfolioId }: { portfolioId: number }) {
  const uiMode = useUiStore((s) => s.uiMode);
  const [editingPosition, setEditingPosition] = useState<PortfolioPosition | null>(null);
  const { data, isLoading } = useSWR(["portfolio", portfolioId], () =>
    portfolioService.detail(portfolioId),
  );

  if (isLoading || !data) return <Skeleton className="h-48 w-full" />;

  const currency = data.base_currency;
  const summary = data.summary;

  return (
    <div className="space-y-4">
      {data.goal_tracking ? <Card><CardHeader title={`Цель ${formatMoney(data.goal_tracking.target, currency)}`} subtitle={`План v${data.goal_tracking.version} · осталось ${data.goal_tracking.time_remaining_months} мес.`}/><CardBody><div className="grid grid-cols-2 gap-4 sm:grid-cols-4"><Stat label="Фактически куплено" value={formatMoney(data.goal_tracking.current,currency)}/><Stat label="Ожидаемая база" value={formatMoney(data.goal_tracking.expected_base,currency)}/><Stat label="Требуется дальше" value={data.goal_tracking.required_return_remaining==null?"—":formatRate(data.goal_tracking.required_return_remaining)}/><Stat label="Статус" value={data.goal_tracking.status === "ON_TRACK" ? "По плану" : data.goal_tracking.status === "NO_EXECUTED_POSITIONS" ? "Покупки не подтверждены" : "Позади плана"} tone={data.goal_tracking.status === "ON_TRACK" ? "positive" : "negative"}/></div><div className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-800"><Button onClick={async()=>{await goalPlannerService.replan(data.goal_tracking!.goal_id);await mutate(["portfolio",portfolioId]);}}>Обновить план</Button><p className="mt-2 text-xs text-slate-500">Создаст новую версию по текущим ценам и фактическим позициям. Старая версия сохранится.</p></div></CardBody></Card> : null}
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

      <PortfolioHistoryChart history={data.history} />

      {data.goal_tracking && data.planned_positions?.length ? <PlannedPositions goalId={data.goal_tracking.goal_id} portfolioId={portfolioId} positions={data.planned_positions} /> : null}

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
                      <button type="button" className="mr-3 text-xs text-slate-400 hover:text-sky-600" onClick={() => setEditingPosition(position)}>изменить</button>
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
          {editingPosition ? <EditPositionForm portfolioId={portfolioId} position={editingPosition} onClose={() => setEditingPosition(null)} /> : null}
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

function PlannedPositions({ goalId, portfolioId, positions }: { goalId: number; portfolioId: number; positions: NonNullable<PortfolioDetail["planned_positions"]> }) {
  const [selected, setSelected] = useState<number | null>(null);
  const current = positions.find(position => position.id === selected);
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [commission, setCommission] = useState("0");
  const [executionDate, setExecutionDate] = useState(new Date().toISOString().slice(0,10));
  const [error, setError] = useState<string | null>(null);

  async function execute() {
    if (!current) return;
    setError(null);
    try {
      await goalPlannerService.markExecuted(goalId,current.id,{actual_quantity:Number(quantity),actual_price:Number(price),actual_commission:Number(commission),execution_date:executionDate});
      setSelected(null); await mutate(["portfolio",portfolioId]); await mutate("portfolios");
    } catch(caught) { setError(caught instanceof Error ? caught.message : "Не удалось подтвердить покупку"); }
  }

  return <Card><CardHeader title="Запланированные покупки" subtitle="Не входят в стоимость и P/L до подтверждения исполнения"/><CardBody><div className="space-y-2">{positions.map(position=><div key={position.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-slate-300 p-3 dark:border-slate-700"><div><span className="mr-2 rounded bg-amber-100 px-2 py-1 text-[10px] font-bold text-amber-700">PLANNED</span><strong>{position.ticker}</strong><p className="mt-1 text-xs text-slate-500">{position.quantity} шт. · ориентир {formatMoney(position.planned_reference_price,"KZT")} · {position.planned_allocation==null?"—":`${(position.planned_allocation*100).toFixed(1)}%`}</p></div><Button onClick={()=>{setSelected(position.id);setQuantity(String(position.quantity));setPrice(String(position.planned_reference_price??""));}}>Отметить как куплено</Button></div>)}</div>{current?<div className="mt-4 grid gap-3 rounded-xl bg-slate-50 p-3 dark:bg-slate-800 sm:grid-cols-4"><Field label="Фактическое количество"><Input type="number" value={quantity} onChange={e=>setQuantity(e.target.value)}/></Field><Field label="Фактическая цена"><Input type="number" value={price} onChange={e=>setPrice(e.target.value)}/></Field><Field label="Комиссия"><Input type="number" value={commission} onChange={e=>setCommission(e.target.value)}/></Field><Field label="Дата исполнения"><Input type="date" value={executionDate} onChange={e=>setExecutionDate(e.target.value)}/></Field><div className="flex gap-2 sm:col-span-4"><Button disabled={!Number(quantity)||!Number(price)} onClick={()=>void execute()}>Подтвердить фактическую покупку</Button><button className="text-xs text-slate-500" onClick={()=>setSelected(null)}>Отмена</button></div>{error&&<p className="text-sm text-rose-600 sm:col-span-4">{error}</p>}</div>:null}</CardBody></Card>;
}

function PortfolioHistoryChart({history}:{history: PortfolioDetail["history"]}) {
  if (history.status !== "available" || history.points.length < 2) {
    return <Card><CardHeader title="История портфеля" subtitle="По сохранённым рыночным наблюдениям за последний год"/><CardBody><EmptyState title={history.status === "unavailable_mixed_currency" ? "Нужна история валютных курсов" : "Истории пока недостаточно"} description={history.status === "unavailable_mixed_currency" ? "Портфель содержит разные валюты, поэтому значения не складываются без фактического FX-ряда." : "График появится после двух или более фактических наблюдений цен."}/></CardBody></Card>;
  }
  const values = history.points.map(point => point.value);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const spread = maximum - minimum || Math.max(maximum * .01, 1);
  const coordinates = history.points.map((point,index) => {
    const x = history.points.length === 1 ? 0 : index / (history.points.length - 1) * 600;
    const y = 165 - (point.value - minimum) / spread * 145;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const first = history.points[0];
  const last = history.points.at(-1)!;
  return <Card><CardHeader title="История портфеля" subtitle="Текущий состав по фактическим сохранённым котировкам · без искусственного заполнения торговых дней"/><CardBody><div className="mb-3 flex flex-wrap justify-between gap-2 text-xs text-slate-500"><span>{formatDate(first.date)} · {formatMoney(first.value, history.currency)}</span><strong className="text-slate-800 dark:text-slate-100">{formatDate(last.date)} · {formatMoney(last.value, history.currency)}</strong></div><svg viewBox="0 0 600 180" role="img" aria-label="График исторической стоимости портфеля" className="h-48 w-full overflow-visible"><path d="M0 165H600" stroke="currentColor" className="text-slate-200 dark:text-slate-700"/><polyline points={coordinates} fill="none" stroke="currentColor" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" className="text-emerald-500"/></svg><p className="mt-2 text-[11px] text-slate-500">Историческая оценка использует текущий состав позиций и последнюю доступную на дату котировку. Это не график денежных потоков или доходности с учётом пополнений.</p></CardBody></Card>;
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
