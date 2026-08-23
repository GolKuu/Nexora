"use client";

import useSWR from "swr";

import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Stat";
import { marketService } from "@/services/market";
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

function StatusCard({label,value: shown,okay}:{label:string;value:string;okay:boolean}) { return <Card><CardBody><div className="flex items-center justify-between gap-2"><p className="text-xs text-slate-500">{label}</p><span className={`h-2.5 w-2.5 rounded-full ${okay ? "bg-emerald-500" : "bg-amber-500"}`}/></div><p className="mt-2 text-lg font-semibold">{shown}</p></CardBody></Card>; }
function Metric({label,value:shown}:{label:string;value:string}) { return <div><p className="text-xs text-slate-500">{label}</p><p className="mt-1 font-semibold tabular">{shown}</p></div>; }
