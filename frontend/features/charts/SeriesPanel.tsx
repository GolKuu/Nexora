"use client";

/** Price, activity and yield history for one instrument.
 *
 *  Every number here comes from ``/series``, which the backend folds out of its
 *  own snapshots of public KASE endpoints. The licensed trading archive is not
 *  involved, and the panel says so in the footer rather than leaving the reader
 *  to assume it.
 *
 *  Chart rules followed deliberately: one measure per plot (never a second
 *  y-axis), a single range filter above everything it scopes, hairline solid
 *  grid, a crosshair tooltip plus keyboard access on every plot, and a table
 *  view so no value is reachable only by hovering.
 */

import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { useSeries } from "@/hooks/useHistory";
import type { InstrumentKind, SeriesResponse, SeriesSession } from "@/types/api";
import { formatCompact, formatMoney, formatNumber, formatPercent, formatRate } from "@/utils/format";

const RANGES: Array<{ label: string; days: number }> = [
  { label: "1М", days: 30 },
  { label: "3М", days: 90 },
  { label: "6М", days: 180 },
  { label: "1 год", days: 365 },
  { label: "Всё", days: 1825 },
];

const AXIS = { fontSize: 11, fill: "var(--viz-ink-muted)" };
const GRID = { stroke: "var(--viz-grid)", strokeWidth: 1 };
const CURSOR = { stroke: "var(--viz-axis)", strokeWidth: 1 };
const PLOT_MARGIN = { top: 12, right: 16, left: 4, bottom: 4 };

const SECTION_LABELS: Record<string, string> = {
  quote: "котировка",
  order_book: "заявки",
  profile: "профиль",
  documents: "документы",
  financials: "финансы",
  coupons: "купоны",
  ratings: "рейтинги",
  corporate_actions: "корпоративные события",
};

function shortDate(value: string): string {
  const [, month, day] = value.split("-");
  return `${day}.${month}`;
}

function sectionLabel(section: string): string {
  return SECTION_LABELS[section] ?? section;
}

/** Tooltip and table share one formatter so the two never disagree. */
function priceText(row: SeriesSession, series: SeriesResponse): string {
  if (row.close === null) return "нет сделок";
  return series.instrument_type === "bond"
    ? `${formatNumber(row.close, 2)}% от номинала`
    : formatMoney(row.close, series.currency, 2);
}

export function SeriesPanel({
  kind,
  identifier,
  title = "История и графики",
}: {
  kind: InstrumentKind;
  identifier: string;
  title?: string;
}) {
  const [days, setDays] = useState(365);
  const [view, setView] = useState<"chart" | "table">("chart");
  const { data, isLoading, isValidating, error } = useSeries(kind, identifier, days);

  const markerByDate = useMemo(
    () => new Map((data?.markers ?? []).map((marker) => [marker.date, marker])),
    [data?.markers],
  );

  if (isLoading && !data) {
    return (
      <Card>
        <CardHeader title={title} />
        <CardBody className="space-y-3">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-64 w-full" />
        </CardBody>
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card>
        <CardHeader title={title} />
        <CardBody>
          <EmptyState
            title="История недоступна"
            description="Публичные данные KASE по этому инструменту ещё не собраны."
          />
        </CardBody>
      </Card>
    );
  }

  const { coverage, sessions } = data;
  const priced = sessions.filter((row) => row.close !== null);
  const withYield = sessions.filter((row) => row.ytm !== null);
  const withActivity = sessions.filter((row) => row.turnover !== null || row.volume !== null);
  const last = priced.at(-1);

  return (
    <div className="viz space-y-3">
      {/* One filter row, above everything it scopes. */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1 rounded-xl border border-slate-200 p-1 dark:border-slate-700">
          {RANGES.map((range) => (
            <button
              key={range.days}
              type="button"
              onClick={() => setDays(range.days)}
              aria-pressed={days === range.days}
              className={
                days === range.days
                  ? "rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-900"
                  : "rounded-lg px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              }
            >
              {range.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1 rounded-xl border border-slate-200 p-1 dark:border-slate-700">
          {(["chart", "table"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setView(mode)}
              aria-pressed={view === mode}
              className={
                view === mode
                  ? "rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-900"
                  : "rounded-lg px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              }
            >
              {mode === "chart" ? "График" : "Таблица"}
            </button>
          ))}
        </div>
        <Badge tone={coverage.licensed_free ? "success" : "warning"}>
          {coverage.licensed_free ? "без лицензионных данных KASE" : "включён лицензионный архив"}
        </Badge>
        {coverage.mock ? <Badge tone="danger">демо-данные</Badge> : null}
      </div>

      <div
        className={
          isValidating && !isLoading
            ? "space-y-4 opacity-60 transition-opacity"
            : "space-y-4 transition-opacity"
        }
      >
        {view === "table" ? (
          <SessionTable data={data} />
        ) : !coverage.chartable ? (
          <Card>
            <CardHeader title={title} subtitle={data.price_unit} />
            <CardBody>
              <EmptyState
                title="Истории пока недостаточно для графика"
                description={
                  data.warning ??
                  "Нужно минимум две сессии с ценой. Данные накапливаются при каждом обновлении публичного фида KASE."
                }
              />
            </CardBody>
          </Card>
        ) : (
          <>
            <Card>
              <CardHeader
                title={kind === "bond" ? "Цена выпуска" : "Цена акции"}
                subtitle={`${data.price_unit} · ${coverage.sessions} сессий`}
                action={
                  last ? (
                    <div className="text-right">
                      <p className="tabular text-lg font-semibold">{priceText(last, data)}</p>
                      <p className="text-xs text-slate-500">
                        {last.change_pct === null
                          ? "к предыдущей сессии — нет данных"
                          : `${last.change_pct >= 0 ? "+" : ""}${formatPercent(last.change_pct, 2)} к предыдущей сессии`}
                      </p>
                    </div>
                  ) : null
                }
              />
              <CardBody>
                <div className="h-72 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={priced} margin={PLOT_MARGIN} accessibilityLayer>
                      <CartesianGrid {...GRID} vertical={false} />
                      <XAxis
                        dataKey="date"
                        tick={AXIS}
                        tickLine={false}
                        axisLine={{ stroke: "var(--viz-axis)" }}
                        tickFormatter={shortDate}
                        minTickGap={28}
                      />
                      <YAxis
                        tick={AXIS}
                        tickLine={false}
                        axisLine={false}
                        width={58}
                        domain={["auto", "auto"]}
                        tickFormatter={(value: number) => formatNumber(value, 2)}
                      />
                      <Tooltip
                        cursor={CURSOR}
                        content={<SessionTooltip series={data} markers={markerByDate} />}
                      />
                      {data.markers.map((marker) => (
                        <ReferenceLine
                          key={marker.date}
                          x={marker.date}
                          stroke="var(--viz-ink-muted)"
                          strokeWidth={1}
                          label={{
                            value: "◆",
                            position: "top",
                            fontSize: 10,
                            fill: "var(--viz-ink-muted)",
                          }}
                        />
                      ))}
                      <Line
                        type="monotone"
                        dataKey="close"
                        stroke="var(--viz-series-1)"
                        strokeWidth={2}
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        dot={false}
                        activeDot={{
                          r: 4,
                          stroke: "var(--viz-surface)",
                          strokeWidth: 2,
                          fill: "var(--viz-series-1)",
                        }}
                        connectNulls
                        name="Цена"
                      />
                      {last ? (
                        <ReferenceDot
                          x={last.date}
                          y={last.close as number}
                          r={4}
                          fill="var(--viz-series-1)"
                          stroke="var(--viz-surface)"
                          strokeWidth={2}
                          label={{
                            value: formatNumber(last.close, 2),
                            position: "left",
                            fontSize: 11,
                            fill: "var(--viz-ink-muted)",
                          }}
                        />
                      ) : null}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                {data.markers.length ? (
                  <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                    ◆ — сессия, в которой зафиксировано реальное изменение данных
                    ({data.markers.length} шт.). Подробности — в истории изменений ниже.
                  </p>
                ) : null}
              </CardBody>
            </Card>

            {withActivity.length ? (
              <Card>
                <CardHeader
                  title="Активность торгов"
                  subtitle="Оборот сессии по публичным итогам KASE"
                />
                <CardBody>
                  <div className="h-56 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={withActivity} margin={PLOT_MARGIN} accessibilityLayer>
                        <CartesianGrid {...GRID} vertical={false} />
                        <XAxis
                          dataKey="date"
                          tick={AXIS}
                          tickLine={false}
                          axisLine={{ stroke: "var(--viz-axis)" }}
                          tickFormatter={shortDate}
                          minTickGap={28}
                        />
                        <YAxis
                          tick={AXIS}
                          tickLine={false}
                          axisLine={false}
                          width={58}
                          tickFormatter={(value: number) => formatCompact(value)}
                        />
                        <Tooltip
                          cursor={{ fill: "var(--viz-grid)", fillOpacity: 0.4 }}
                          content={<SessionTooltip series={data} markers={markerByDate} activity />}
                        />
                        <Bar
                          dataKey="turnover"
                          fill="var(--viz-series-1)"
                          maxBarSize={24}
                          radius={[4, 4, 0, 0]}
                          name="Оборот"
                        />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </CardBody>
              </Card>
            ) : null}

            {kind === "bond" && withYield.length > 1 ? (
              <Card>
                <CardHeader
                  title="Доходность к погашению"
                  subtitle="YTM по итогам сессии, % годовых"
                />
                <CardBody>
                  <div className="h-56 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={withYield} margin={PLOT_MARGIN} accessibilityLayer>
                        <CartesianGrid {...GRID} vertical={false} />
                        <XAxis
                          dataKey="date"
                          tick={AXIS}
                          tickLine={false}
                          axisLine={{ stroke: "var(--viz-axis)" }}
                          tickFormatter={shortDate}
                          minTickGap={28}
                        />
                        <YAxis
                          tick={AXIS}
                          tickLine={false}
                          axisLine={false}
                          width={58}
                          domain={["auto", "auto"]}
                          tickFormatter={(value: number) => formatNumber(value * 100, 1)}
                        />
                        <Tooltip
                          cursor={CURSOR}
                          content={<SessionTooltip series={data} markers={markerByDate} yieldOnly />}
                        />
                        <Line
                          type="monotone"
                          dataKey="ytm"
                          stroke="var(--viz-series-2)"
                          strokeWidth={2}
                          strokeLinecap="round"
                          dot={false}
                          activeDot={{
                            r: 4,
                            stroke: "var(--viz-surface)",
                            strokeWidth: 2,
                            fill: "var(--viz-series-2)",
                          }}
                          connectNulls
                          name="YTM"
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </CardBody>
              </Card>
            ) : null}
          </>
        )}

        <CoverageNote data={data} />
      </div>
    </div>
  );
}

function SessionTooltip({
  active,
  payload,
  series,
  markers,
  activity,
  yieldOnly,
}: {
  active?: boolean;
  payload?: Array<{ payload: SeriesSession }>;
  series: SeriesResponse;
  markers: Map<string, { count: number; sections: string[] }>;
  activity?: boolean;
  yieldOnly?: boolean;
}) {
  const row = active ? payload?.[0]?.payload : undefined;
  if (!row) return null;
  const marker = markers.get(row.date);
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg dark:border-slate-700 dark:bg-slate-900">
      <p className="font-medium text-slate-500 dark:text-slate-400">{row.date}</p>
      {!yieldOnly ? (
        <p className="tabular mt-1 flex items-center gap-2 text-sm font-semibold">
          <span
            aria-hidden
            className="inline-block h-0.5 w-4 rounded"
            style={{ background: "var(--viz-series-1)" }}
          />
          {activity
            ? row.turnover === null
              ? "оборот не опубликован"
              : formatMoney(row.turnover, series.currency, 0)
            : priceText(row, series)}
        </p>
      ) : null}
      {row.ytm !== null && (yieldOnly || series.instrument_type === "bond") ? (
        <p className="tabular mt-1 flex items-center gap-2 text-sm font-semibold">
          <span
            aria-hidden
            className="inline-block h-0.5 w-4 rounded"
            style={{ background: "var(--viz-series-2)" }}
          />
          YTM {formatRate(row.ytm, 2)}
        </p>
      ) : null}
      <dl className="mt-1 space-y-0.5 text-slate-500 dark:text-slate-400">
        {row.open !== null && row.high !== null && row.low !== null && !activity ? (
          <div className="tabular">
            откр. {formatNumber(row.open, 2)} · макс. {formatNumber(row.high, 2)} · мин.{" "}
            {formatNumber(row.low, 2)}
          </div>
        ) : null}
        {row.change_pct !== null && !activity ? (
          <div className="tabular">
            к пред. сессии {row.change_pct >= 0 ? "+" : ""}
            {formatPercent(row.change_pct, 2)}
          </div>
        ) : null}
        {row.trades !== null ? <div className="tabular">сделок: {row.trades}</div> : null}
        {row.bid !== null || row.ask !== null ? (
          <div className="tabular">
            bid/ask {formatNumber(row.bid, 2)} / {formatNumber(row.ask, 2)}
          </div>
        ) : null}
        <div>
          {row.bar_basis === "native"
            ? "бар опубликован биржей"
            : `собрано из наших наблюдений (${row.observations})`}
        </div>
        {marker ? (
          <div>
            ◆ изменений: {marker.count} ({marker.sections.map(sectionLabel).join(", ")})
          </div>
        ) : null}
      </dl>
    </div>
  );
}

function SessionTable({ data }: { data: SeriesResponse }) {
  const isBond = data.instrument_type === "bond";
  return (
    <Card>
      <CardHeader
        title="Таблица сессий"
        subtitle="Те же значения, что на графиках — доступны без наведения"
      />
      <CardBody className="overflow-x-auto px-0">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-5 py-2 font-medium">Дата</th>
              <th className="px-3 py-2 text-right font-medium">Откр.</th>
              <th className="px-3 py-2 text-right font-medium">Макс.</th>
              <th className="px-3 py-2 text-right font-medium">Мин.</th>
              <th className="px-3 py-2 text-right font-medium">Закр.</th>
              <th className="px-3 py-2 text-right font-medium">Δ</th>
              {isBond ? <th className="px-3 py-2 text-right font-medium">YTM</th> : null}
              <th className="px-3 py-2 text-right font-medium">Оборот</th>
              <th className="px-3 py-2 text-right font-medium">Сделок</th>
              <th className="px-3 py-2 font-medium">Основа</th>
              <th className="px-5 py-2 text-right font-medium">◆</th>
            </tr>
          </thead>
          <tbody className="tabular divide-y divide-slate-100 dark:divide-slate-800">
            {[...data.sessions].reverse().map((row) => (
              <tr key={row.date}>
                <td className="px-5 py-2">{row.date}</td>
                <td className="px-3 py-2 text-right">{formatNumber(row.open, 2)}</td>
                <td className="px-3 py-2 text-right">{formatNumber(row.high, 2)}</td>
                <td className="px-3 py-2 text-right">{formatNumber(row.low, 2)}</td>
                <td className="px-3 py-2 text-right">{formatNumber(row.close, 2)}</td>
                <td className="px-3 py-2 text-right">{formatPercent(row.change_pct, 2)}</td>
                {isBond ? (
                  <td className="px-3 py-2 text-right">{formatRate(row.ytm, 2)}</td>
                ) : null}
                <td className="px-3 py-2 text-right">{formatCompact(row.turnover)}</td>
                <td className="px-3 py-2 text-right">{row.trades ?? "—"}</td>
                <td className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400">
                  {row.bar_basis === "native" ? "биржевой бар" : `наблюдений: ${row.observations}`}
                </td>
                <td className="px-5 py-2 text-right">{row.change_events || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.sessions.length === 0 ? (
          <EmptyState
            title="Сессий пока нет"
            description="Данные появятся после первого обновления публичного фида KASE."
          />
        ) : null}
      </CardBody>
    </Card>
  );
}

function CoverageNote({ data }: { data: SeriesResponse }) {
  const { coverage } = data;
  const sources = Object.keys(coverage.sources);
  return (
    <Card>
      <CardBody className="space-y-1 text-xs text-slate-500 dark:text-slate-400">
        <p>
          Сессий с данными: <strong className="tabular">{coverage.sessions}</strong>; торговых
          дней в периоде: <span className="tabular">{coverage.expected_sessions}</span>
          {coverage.coverage_ratio !== null
            ? `, покрытие ${formatPercent(coverage.coverage_ratio * 100, 0)}`
            : ""}
          {coverage.first_session
            ? `; период с ${coverage.first_session} по ${coverage.last_session}`
            : ""}
          . Наблюдений: <span className="tabular">{coverage.observations}</span>
          {coverage.longest_gap_sessions
            ? `; максимальный пропуск — ${coverage.longest_gap_sessions} сессий`
            : ""}
          {coverage.sessions_outside_calendar
            ? `; вне торгового календаря — ${coverage.sessions_outside_calendar}`
            : ""}
          .
        </p>
        <p>
          Баров, опубликованных биржей: <span className="tabular">{coverage.native_bars}</span>;
          собранных из наших наблюдений:{" "}
          <span className="tabular">{coverage.sampled_bars}</span>. Источники:{" "}
          {sources.length ? sources.join(", ") : "нет данных"}.
        </p>
        <p>
          {coverage.licensed_free
            ? `Лицензионный архив KASE не используется${
                coverage.licensed_rows_excluded
                  ? ` (исключено строк: ${coverage.licensed_rows_excluded})`
                  : ""
              }.`
            : "В серию включены строки лицензионного архива KASE."}
        </p>
        {data.warning ? (
          <p className="text-amber-700 dark:text-amber-400">{data.warning}</p>
        ) : null}
      </CardBody>
    </Card>
  );
}
