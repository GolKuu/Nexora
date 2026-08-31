"use client";

import useSWR from "swr";

import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Stat";
import { marketService } from "@/services/market";
import type { SubsystemHealth } from "@/types/api";
import { formatDate, formatNumber } from "@/utils/format";

function value(object: Record<string, unknown> | undefined, key: string) {
  const result = object?.[key];
  return result === null || result === undefined ? "—" : String(result);
}

export function SystemDashboard() {
  const health = useSWR("system-health", marketService.health, { refreshInterval: 30_000 });
  const monitoring = useSWR("system-monitoring", marketService.monitoring, { refreshInterval: 30_000 });
  const sources = useSWR("system-sources", marketService.sources, { refreshInterval: 60_000 });
  const ingestion = useSWR("system-ingestion", marketService.ingestion, { refreshInterval: 60_000 });
  const subsystems = useSWR("system-subsystems", marketService.subsystems, { refreshInterval: 30_000 });
  if (health.isLoading || monitoring.isLoading) return <Skeleton className="h-80 w-full"/>;

  const sourceRows = Array.isArray(sources.data?.sources) ? sources.data.sources as Array<Record<string, unknown>> : [];
  const db = (health.data?.database ?? {}) as Record<string, unknown>;
  const okay = health.data?.status === "ok";
  const monitoringOkay = monitoring.data?.status === "ok";
  return <div className="space-y-4">
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatusCard label="Приложение" value={value(health.data, "status")} okay={okay}/>
      <StatusCard label="База данных" value={db.ok ? "ok" : "error"} okay={db.ok === true}/>
      <StatusCard label="Мониторинг" value={value(monitoring.data, "status")} okay={monitoringOkay}/>
      <StatusCard label="Режим KASE" value={value(health.data, "kase_data_mode")} okay={health.data?.kase_data_mode !== "mock"}/>
    </div>
    <Card><CardHeader title="Подсистемы" subtitle="Статус каждой подсистемы выводится из её собственных записей. Компонент без свидетельств запуска показывает «не запускалась», а не зелёный по умолчанию."/>
      {subsystems.error
        ? <CardBody><p className="text-sm text-rose-600">Не удалось загрузить состояние подсистем. <button type="button" onClick={() => void subsystems.mutate()} className="underline">Повторить</button></p></CardBody>
        : !subsystems.data
          ? <CardBody className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Skeleton className="h-24 w-full"/><Skeleton className="h-24 w-full"/><Skeleton className="h-24 w-full"/><Skeleton className="h-24 w-full"/></CardBody>
          : <CardBody>
              <div data-testid="subsystems" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {subsystems.data.components.map(component => <SubsystemCard key={component.code} component={component}/>)}
              </div>
            </CardBody>}
      {subsystems.data?.serverless
        ? <CardBody className="border-t border-slate-100 pt-3 text-xs text-slate-500 dark:border-slate-800">Развёртывание serverless: фоновый планировщик здесь не работает, данные поступают из снапшота. Поэтому сборщики показывают «не запускалась» — это ожидаемо, а не сбой.</CardBody>
        : null}
    </Card>
    <Card><CardHeader title="Цикл мониторинга" subtitle="Фактическая телеметрия сохранённых серверных циклов, а не только настройка планировщика."/><CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Metric label="Последний запуск" value={formatDate(value(monitoring.data, "last_cycle_at"))}/>
      <Metric label="Последний успех" value={formatDate(value(monitoring.data, "last_successful_cycle_at"))}/>
      <Metric label="Проверено инструментов" value={value(monitoring.data, "instruments_checked")}/>
      <Metric label="Изменилось" value={value(monitoring.data, "instruments_changed")}/>
      <Metric label="Ошибки" value={value(monitoring.data, "failures")}/>
      <Metric label="Аномалии парсера" value={value(monitoring.data, "parser_anomalies")}/>
      <Metric label="Следующий запуск" value={formatDate(value(monitoring.data, "next_cycle_at"))}/>
      <Metric label="Интервал" value={`${value(monitoring.data, "interval_seconds")} сек.`}/>
    </CardBody></Card>
    <Card><CardHeader title="Сбор за последние 24 часа" subtitle="Неизменившиеся страницы не запускают глубокое извлечение и AI-анализ повторно."/><CardBody className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
      <Metric label="Проверок" value={value(ingestion.data, "pages_checked")}/><Metric label="Изменений" value={value(ingestion.data, "pages_changed")}/><Metric label="Без изменений" value={value(ingestion.data, "pages_unchanged")}/><Metric label="Новых сделок" value={value(ingestion.data, "new_trades")}/><Metric label="Ошибок парсера" value={value(ingestion.data, "parser_errors")}/><Metric label="Средняя задержка" value={`${formatNumber(ingestion.data?.average_check_latency_ms as number | null)} мс`}/>
    </CardBody></Card>
    <Card><CardHeader title="Источники данных" subtitle="Для каждого источника показаны последнее успешное обращение и ошибка."/><div>{sourceRows.length ? sourceRows.map(row => <div key={String(row.code)} className="grid gap-2 border-t border-slate-100 px-4 py-3 text-sm first:border-0 dark:border-slate-800 sm:grid-cols-[1fr_auto_auto] sm:items-center"><div><p className="font-semibold">{String(row.name ?? row.code)}</p><p className="text-xs text-slate-500">{String(row.kind ?? "—")} · {row.is_authoritative ? "официальный" : "дополнительный"}</p></div><Badge tone={row.is_enabled ? "success" : "warning"}>{row.is_enabled ? "включён" : "выключен"}</Badge><p className="text-xs text-slate-500">успех: {formatDate(row.last_success_at as string | null)}</p>{row.last_error ? <p className="text-xs text-rose-600 sm:col-span-3">{String(row.last_error)}</p> : null}</div>) : <CardBody><p className="text-sm text-slate-500">Источники появятся после первого успешного сбора.</p></CardBody>}</div></Card>
  </div>;
}

const SUBSYSTEM_LABELS: Record<string, string> = {
  database: "База данных", kase_collector: "Сборщик KASE", monitoring: "Мониторинг",
  news: "Новости", dcf: "DCF", technical_analysis: "Технический анализ",
  parser: "Парсер", scheduler: "Планировщик",
};
const STATUS_LABELS: Record<string, string> = {
  ok: "работает", degraded: "с ошибками", stalled: "остановлена",
  never_run: "не запускалась", disabled: "выключена",
};
/** Green is earned, never assumed: only a subsystem reporting `ok` gets it. */
function statusTone(status: string): string {
  if (status === "ok") return "bg-emerald-500";
  if (status === "degraded" || status === "stalled") return "bg-rose-500";
  return "bg-slate-400";
}

function SubsystemCard({ component }: { component: SubsystemHealth }) {
  return <div className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
    <div className="flex items-center justify-between gap-2">
      <p className="text-xs font-medium text-slate-500">{SUBSYSTEM_LABELS[component.code] ?? component.code}</p>
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${statusTone(component.status)}`}/>
    </div>
    <p className="mt-1 text-sm font-semibold">{STATUS_LABELS[component.status] ?? component.status}</p>
    <dl className="mt-2 space-y-0.5 text-[11px] text-slate-500">
      <div className="flex justify-between gap-2"><dt>успех</dt><dd className="tabular">{formatDate(component.last_success_at)}</dd></div>
      <div className="flex justify-between gap-2"><dt>сбой</dt><dd className="tabular">{formatDate(component.last_failure_at)}</dd></div>
      <div className="flex justify-between gap-2"><dt>задержка</dt><dd className="tabular">{component.latency_ms == null ? "—" : `${formatNumber(component.latency_ms)} мс`}</dd></div>
      <div className="flex justify-between gap-2"><dt>следующий</dt><dd className="tabular">{formatDate(component.next_run_at)}</dd></div>
    </dl>
    {component.reason ? <p className="mt-2 text-[11px] text-slate-500">{component.reason}</p> : null}
    {component.last_error ? <p className="mt-2 text-[11px] text-rose-600">{component.last_error}</p> : null}
  </div>;
}

function StatusCard({label,value: shown,okay}:{label:string;value:string;okay:boolean}) { return <Card><CardBody><div className="flex items-center justify-between gap-2"><p className="text-xs text-slate-500">{label}</p><span className={`h-2.5 w-2.5 rounded-full ${okay ? "bg-emerald-500" : "bg-amber-500"}`}/></div><p className="mt-2 text-lg font-semibold">{shown}</p></CardBody></Card>; }
function Metric({label,value:shown}:{label:string;value:string}) { return <div><p className="text-xs text-slate-500">{label}</p><p className="mt-1 font-semibold tabular">{shown}</p></div>; }
