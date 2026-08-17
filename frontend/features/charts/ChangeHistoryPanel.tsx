"use client";

/** The instrument's real change history.
 *
 *  Rows come from ``data_change_sets`` - written only when normalised public
 *  source data actually differed from what we already had. A row is therefore
 *  evidence of a change with a source link, not a periodic log entry.
 */

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { useChangeSummary, useChanges } from "@/hooks/useHistory";
import type { ChangeRecord, InstrumentKind } from "@/types/api";
import { formatNumber } from "@/utils/format";

/** A busy share can log hundreds of field changes a day; the feed opens with a
 *  readable slice and grows on request instead of burying the rest of the page. */
const PAGE_SIZE = 40;

const SECTION_LABELS: Record<string, string> = {
  quote: "Котировки",
  order_book: "Заявки",
  profile: "Профиль",
  documents: "Документы",
  financials: "Финансы",
  coupons: "Купоны",
  ratings: "Рейтинги",
  corporate_actions: "Корпоративные события",
  dividends: "Дивиденды",
  scores: "Оценки",
  metrics: "Показатели",
  news: "Новости",
};

/** Only the recurring machine names get a Russian label; anything else is shown
 *  as the backend named it rather than guessed at. */
const FIELD_LABELS: Record<string, string> = {
  bid: "лучшая заявка на покупку",
  ask: "лучшая заявка на продажу",
  last: "последняя сделка",
  close: "цена закрытия",
  price: "цена",
  clean_price: "чистая цена",
  ytm: "доходность к погашению",
  turnover: "оборот",
  volume: "объём",
  number_of_trades: "число сделок",
  liquidity_class: "класс ликвидности",
  data_quality: "полнота данных",
  investment: "инвестиционная оценка",
  quality: "качество",
  valuation: "оценка стоимости",
  growth: "рост",
  dividend: "дивиденды",
  liquidity: "ликвидность",
  risk: "риск",
  momentum: "моментум",
};

const CHANGE_TYPES: Record<string, string> = {
  created: "добавлено",
  updated: "изменено",
  deleted: "удалено",
};

function label(section: string): string {
  return SECTION_LABELS[section] ?? section;
}

function fieldName(field: string): string {
  const name = field.split(".").at(-1) ?? field;
  return FIELD_LABELS[name] ?? name;
}

function isEmpty(value: unknown): boolean {
  return value === null || value === undefined || value === "";
}

/** Values arrive as arbitrary JSON; render them readably without inventing precision. */
function renderValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return formatNumber(value, Number.isInteger(value) ? 0 : 4);
  if (typeof value === "boolean") return value ? "да" : "нет";
  if (typeof value === "string") return value.length > 120 ? `${value.slice(0, 120)}…` : value;
  return JSON.stringify(value).slice(0, 160);
}

function timeText(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function ChangeHistoryPanel({
  kind,
  identifier,
}: {
  kind: InstrumentKind;
  identifier: string;
}) {
  const [section, setSection] = useState<string>("");
  const [materialOnly, setMaterialOnly] = useState(false);
  const [visible, setVisible] = useState(PAGE_SIZE);
  const { data: summary } = useChangeSummary(kind, identifier);
  const { data, isLoading, isValidating } = useChanges(kind, identifier, section || undefined);

  const sections = useMemo(() => {
    const known = new Set(summary?.summary.sections ?? []);
    (data ?? []).forEach((row) => known.add(row.section));
    return [...known].sort();
  }, [data, summary?.summary.sections]);

  const rows = useMemo(
    () => (materialOnly ? (data ?? []).filter((row) => row.material) : (data ?? [])),
    [data, materialOnly],
  );

  const grouped = useMemo(() => {
    const days = new Map<string, ChangeRecord[]>();
    rows.slice(0, visible).forEach((row) => {
      const day = row.detected_at.slice(0, 10);
      days.set(day, [...(days.get(day) ?? []), row]);
    });
    return [...days.entries()];
  }, [rows, visible]);

  return (
    <Card>
      <CardHeader
        title="История изменений"
        subtitle="Только зафиксированные различия в публичных данных KASE, со ссылкой на источник"
        action={
          summary ? (
            <div className="text-right text-xs text-slate-500 dark:text-slate-400">
              <p>
                Существенных: <strong className="tabular">{summary.material_changes}</strong>
              </p>
              {summary.freshness?.last_changed_at ? (
                <p>Последнее: {timeText(summary.freshness.last_changed_at)}</p>
              ) : null}
            </div>
          ) : null
        }
      />
      <CardBody className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={section}
            onChange={(event) => {
              setSection(event.target.value);
              setVisible(PAGE_SIZE);
            }}
            aria-label="Раздел данных"
            className="rounded-xl border border-slate-200 bg-transparent px-3 py-1.5 text-xs dark:border-slate-700"
          >
            <option value="">Все разделы</option>
            {sections.map((value) => (
              <option key={value} value={value}>
                {label(value)}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
            <input
              type="checkbox"
              checked={materialOnly}
              onChange={(event) => {
                setMaterialOnly(event.target.checked);
                setVisible(PAGE_SIZE);
              }}
              className="h-4 w-4 rounded border-slate-300 dark:border-slate-600"
            />
            только существенные
          </label>
          {summary?.freshness?.last_checked_at ? (
            <span className="text-xs text-slate-500 dark:text-slate-400">
              проверено: {timeText(summary.freshness.last_checked_at)}
            </span>
          ) : null}
        </div>

        {isLoading && !data ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : grouped.length === 0 ? (
          <EmptyState
            title="Изменений не зафиксировано"
            description="Каждая проверка публичного источника сравнивается с сохранённым состоянием. Пока различий не было."
          />
        ) : (
          <ol
            className={
              isValidating && !isLoading
                ? "space-y-4 opacity-60 transition-opacity"
                : "space-y-4 transition-opacity"
            }
          >
            {grouped.map(([day, items]) => (
              <li key={day}>
                <div className="mb-2 flex items-center gap-2">
                  <h4 className="text-sm font-semibold">{timeText(day).slice(0, 10)}</h4>
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    {items.length} изменени{items.length === 1 ? "е" : "й"}
                  </span>
                </div>
                <ul className="space-y-2 border-l border-slate-200 pl-4 dark:border-slate-700">
                  {items.map((row) => (
                    <li key={row.id} className="text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={row.material ? "warning" : "neutral"}>
                          {label(row.section)}
                        </Badge>
                        <span className="font-medium">{fieldName(row.field)}</span>
                        <span className="text-xs text-slate-500 dark:text-slate-400">
                          {CHANGE_TYPES[row.change_type] ?? row.change_type} ·{" "}
                          {timeText(row.detected_at).slice(11)} · важность{" "}
                          <span className="tabular">{row.importance}</span>
                        </span>
                      </div>
                      <p className="tabular mt-0.5 text-slate-600 dark:text-slate-300">
                        {isEmpty(row.old_value) && isEmpty(row.new_value) ? (
                          // Recorded appearance of a field the source left blank:
                          // "— → —" would look like a rendering bug.
                          <span>показатель появился, но значение не опубликовано</span>
                        ) : (
                          <>
                            {renderValue(row.old_value)} →{" "}
                            <strong>{renderValue(row.new_value)}</strong>
                          </>
                        )}
                      </p>
                      {row.source_url ? (
                        <a
                          href={row.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs underline decoration-dotted"
                        >
                          источник ↗
                        </a>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        )}

        {rows.length > visible ? (
          <button
            type="button"
            onClick={() => setVisible((count) => count + PAGE_SIZE)}
            className="w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-medium hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
          >
            Показать ещё · осталось {rows.length - visible}
          </button>
        ) : null}
      </CardBody>
    </Card>
  );
}
