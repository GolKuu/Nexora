"use client";

import useSWR from "swr";

import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { historyService } from "@/services/history";
import { formatDate, formatNumber } from "@/utils/format";

export function ScoreHistoryPanel({ identifier }: { identifier: string }) {
  const { data, isLoading } = useSWR(["score-history", identifier], () => historyService.scoreHistory(identifier), { revalidateOnFocus: false });
  if (isLoading) return <Card><CardBody><Skeleton className="h-44 w-full"/></CardBody></Card>;
  if (!data?.snapshots.length) return <Card><CardHeader title="История score" subtitle="Point-in-time оценки сохраняются навсегда и не пересчитываются задним числом."/><CardBody><EmptyState title="Истории оценок пока нет" description="Первый снимок появится после строгого расчёта score."/></CardBody></Card>;
  const chronological = [...data.snapshots].reverse();
  const points = chronological.map((row,index) => `${chronological.length === 1 ? 300 : 20 + index * 560 / (chronological.length - 1)},${145 - row.final_score * 1.25}`).join(" ");
  return <Card><CardHeader title="История score" subtitle={data.note}/><CardBody className="space-y-4">
    <div className="viz overflow-hidden rounded-xl bg-[var(--viz-surface)]"><svg viewBox="0 0 600 170" role="img" aria-label="График истории Investment Score" className="h-44 w-full"><line x1="20" y1="20" x2="20" y2="145" stroke="var(--viz-axis)"/><line x1="20" y1="145" x2="580" y2="145" stroke="var(--viz-axis)"/>{[25,50,75,100].map(v=><g key={v}><line x1="20" y1={145-v*1.25} x2="580" y2={145-v*1.25} stroke="var(--viz-grid)"/><text x="24" y={140-v*1.25} fill="var(--viz-ink-muted)" fontSize="10">{v}</text></g>)}<polyline points={points} fill="none" stroke="var(--viz-series-1)" strokeWidth="3" strokeLinejoin="round"/>{chronological.map((row,index)=><circle key={row.id} cx={chronological.length === 1 ? 300 : 20 + index*560/(chronological.length-1)} cy={145-row.final_score*1.25} r="4" fill="var(--viz-series-1)"/>)}</svg></div>
    <div className="grid gap-2 sm:grid-cols-2">{data.snapshots.slice(0,8).map(row=><div key={row.id} className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800"><div className="flex items-center justify-between"><span className="text-xs text-slate-500">{formatDate(row.as_of ?? row.calculated_at)}</span><Badge>{row.kind}</Badge></div><p className="mt-1 text-xl font-semibold tabular">{formatNumber(row.final_score,1)}/100</p><p className="text-xs text-slate-500">confidence {formatNumber(row.confidence,1)} · Data Quality {formatNumber(row.data_quality,1)} · {row.model_version}</p></div>)}</div>
    {data.transitions.slice(0,5).map(change=><div key={change.to_snapshot_id} className="rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-700"><p className="font-semibold"><span className={change.direction === "up" ? "text-emerald-600" : change.direction === "down" ? "text-rose-600" : ""}>{change.delta == null ? "—" : `${change.delta > 0 ? "+" : ""}${change.delta}`}</span> · {change.from} → {change.to}</p><p className="mt-1 text-xs text-slate-500">{change.components_changed.slice(0,3).map(row => row.label ?? row.code).join(", ") || "Компоненты не изменились"}{change.red_flags_raised.length ? ` · новые red flags: ${change.red_flags_raised.map(row=>row.code).join(", ")}` : ""}{change.caps_applied.length ? ` · hard caps: ${change.caps_applied.map(row=>row.code).join(", ")}` : ""}</p></div>)}
  </CardBody></Card>;
}
