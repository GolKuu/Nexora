"use client";

import { useMemo, useState } from "react";
import { Area, Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useStockForecast } from "@/hooks/useStocks";
import { formatDate, formatMoney, formatRate } from "@/utils/format";

const HORIZONS = [{key: "1d", label: "1Д"}, {key: "5d", label: "5Д"}, {key: "20d", label: "1М"}, {key: "60d", label: "3М"}];
const FACTORS: Record<string, string> = {
  return_1d: "Движение за день", return_5d: "Импульс за неделю", return_20d: "Импульс за месяц",
  return_60d: "Долгий импульс", momentum_5_20: "Ускорение тренда", distance_ma20: "Отклонение от средней",
  drawdown_60: "Просадка", range_20: "Ценовой диапазон", volatility_20: "Краткосрочная волатильность",
  volatility_60: "Долгосрочная волатильность", relative_volume_20: "Относительный объём",
  volume_trend: "Тренд объёма", spread_pct: "Биржевой спред", trades_log: "Частота сделок",
  valuation_pe: "Оценка P/E", fundamental_roe: "Рентабельность капитала", event_sentiment: "Тон событий",
  event_importance: "Важность событий", event_surprise: "Неожиданность события", event_count_5d: "Плотность новостей",
  market_regime: "Рыночный режим",
};

function pct(value?: number) { return value == null ? "—" : formatRate(value, 1); }
function dayLabel(value: string) { return new Intl.DateTimeFormat("ru-RU", {day: "2-digit", month: "short"}).format(new Date(value)); }

export function ForecastPanel({ticker, currency}: {ticker: string; currency: string}) {
  const [horizon, setHorizon] = useState("20d");
  const {data, isLoading, error} = useStockForecast(ticker, horizon);
  const selected = data?.horizons?.[horizon];
  const available = HORIZONS.filter((item) => data?.horizons?.[item.key]?.forecast_available);
  const chart = useMemo(() => {
    if (!data) return [];
    const history = data.history.map((point) => ({date: point.date, history: point.price, volume: point.volume}));
    if (!data.path.length || data.current_price == null || !data.as_of) return history;
    const current = {date: data.as_of, history: data.current_price, forecast: data.current_price, q10: data.current_price, q25: data.current_price, q75: data.current_price, q90: data.current_price, base80: data.current_price, band80: 0, base50: data.current_price, band50: 0};
    const future = data.path.map((point) => ({...point, forecast: point.median, base80: point.q10, band80: point.q90 - point.q10, base50: point.q25, band50: point.q75 - point.q25}));
    return [...history.slice(0, -1), current, ...future];
  }, [data]);

  if (isLoading) return <Card><CardBody><div className="h-[430px] animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" /></CardBody></Card>;
  if (error || !data) return <Card><CardBody><p className="text-sm text-rose-600">Не удалось загрузить прогнозный контур.</p></CardBody></Card>;

  return <Card className="overflow-hidden">
    <CardHeader
      title={<span className="flex items-center gap-2">Прогноз модели <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">Quant AI</span></span>}
      subtitle="Вероятностный сценарий, не гарантия будущей цены · автообновление каждые 10 минут"
      action={<div className="flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800">{available.map((item) => <button key={item.key} onClick={() => setHorizon(item.key)} className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${horizon === item.key ? "bg-white text-slate-950 shadow-sm dark:bg-slate-700 dark:text-white" : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}>{item.label}</button>)}</div>}
    />
    <CardBody className="p-0">
      <div className="grid gap-px border-b border-slate-100 bg-slate-100 sm:grid-cols-4 dark:border-slate-800 dark:bg-slate-800">
        <ForecastStat label="Центральный сценарий" value={pct(selected?.median_return)} accent={selected?.median_return != null && selected.median_return >= 0} />
        <ForecastStat label="Вероятность роста" value={pct(selected?.probability_up)} />
        <ForecastStat label="80% диапазон" value={selected?.q10 == null ? "—" : `${pct(selected.q10)} … ${pct(selected.q90)}`} />
        <ForecastStat label="Уверенность модели" value={pct(selected?.confidence)} />
      </div>

      {!selected?.forecast_available ? <div className="m-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
        Прогноз для выбранного горизонта пока недоступен: требуется не менее {selected?.minimum_observations ?? "достаточного числа"} реальных торговых наблюдений, сейчас {selected?.observations ?? data.history.length}. История не дополняется искусственными ценами.
      </div> : null}

      <div className="px-2 pt-5 sm:px-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 px-3 text-xs text-slate-500">
          <div className="flex items-center gap-4"><Legend color="#16a34a" label="История" /><Legend color="#2563eb" label="Медиана модели" dashed /><Legend color="#93c5fd" label="50% диапазон" /><Legend color="#dbeafe" label="80% диапазон" /></div>
          <span>NOW · {formatDate(data.as_of)}</span>
        </div>
        <div className="h-[330px] w-full"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={chart} margin={{top: 8, right: 12, bottom: 2, left: 2}}>
          <defs><linearGradient id="forecast80" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#60a5fa" stopOpacity={0.24}/><stop offset="100%" stopColor="#60a5fa" stopOpacity={0.06}/></linearGradient><linearGradient id="forecast50" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#2563eb" stopOpacity={0.34}/><stop offset="100%" stopColor="#2563eb" stopOpacity={0.14}/></linearGradient></defs>
          <CartesianGrid strokeDasharray="2 4" stroke="#94a3b8" opacity={0.18} vertical={false} />
          <XAxis dataKey="date" tickFormatter={dayLabel} minTickGap={46} tick={{fontSize: 11, fill: "#64748b"}} axisLine={false} tickLine={false} />
          <YAxis domain={["auto", "auto"]} orientation="right" tick={{fontSize: 11, fill: "#64748b"}} tickFormatter={(value) => Number(value).toLocaleString("ru-RU", {maximumFractionDigits: 2})} axisLine={false} tickLine={false} width={58} />
          <Tooltip content={<ForecastTooltip currency={currency} />} />
          <Area type="monotone" dataKey="base80" stackId="outer" stroke="none" fill="transparent" connectNulls={false} isAnimationActive={false} />
          <Area type="monotone" dataKey="band80" stackId="outer" stroke="none" fill="url(#forecast80)" connectNulls={false} isAnimationActive={false} />
          <Area type="monotone" dataKey="base50" stackId="inner" stroke="none" fill="transparent" connectNulls={false} isAnimationActive={false} />
          <Area type="monotone" dataKey="band50" stackId="inner" stroke="none" fill="url(#forecast50)" connectNulls={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="history" stroke="#16a34a" strokeWidth={2.4} dot={false} activeDot={{r: 4}} connectNulls={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="forecast" stroke="#2563eb" strokeWidth={2.4} strokeDasharray="7 6" dot={false} activeDot={{r: 4}} connectNulls={false} isAnimationActive={false} />
          {data.as_of ? <ReferenceLine x={data.as_of} stroke="#64748b" strokeDasharray="4 5" label={{value: "NOW", position: "insideTopRight", fill: "#64748b", fontSize: 10}} /> : null}
        </ComposedChart></ResponsiveContainer></div>
        <div className="h-[86px] w-full border-t border-slate-100 dark:border-slate-800"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={chart} margin={{top: 8, right: 70, bottom: 4, left: 2}}><XAxis dataKey="date" hide/><YAxis hide/><Tooltip content={<VolumeTooltip/>}/><Bar dataKey="volume" fill="#22c55e" opacity={0.65} radius={[2,2,0,0]} /></ComposedChart></ResponsiveContainer></div>
      </div>

      <div className="grid gap-4 border-t border-slate-100 p-5 md:grid-cols-[1.3fr_1fr] dark:border-slate-800">
        <div><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Что связано с оценкой модели</p><div className="mt-2 flex flex-wrap gap-2">{(data.explanation ?? []).slice(0, 5).map((factor) => <span key={factor.feature} className={`rounded-full px-2.5 py-1 text-xs ${factor.association === "positive" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" : "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300"}`}>{factor.association === "positive" ? "↗" : "↘"} {FACTORS[factor.feature] ?? factor.feature}</span>)}</div><p className="mt-2 text-[11px] text-slate-500">Факторы показывают статистическую связь, а не доказанную причинность.</p></div>
        <div className="text-xs text-slate-500"><p>Модель: <span className="font-mono text-slate-700 dark:text-slate-300">{data.model_version}</span></p><p className="mt-1">Данные: {data.data_mode} · {formatDate(data.source_timestamp)}</p>{data.warnings.map((warning) => <p key={warning} className="mt-1 text-amber-700 dark:text-amber-300">⚠ {warning}</p>)}</div>
      </div>
    </CardBody>
  </Card>;
}

function ForecastStat({label, value, accent}: {label: string; value: string; accent?: boolean}) { return <div className="bg-white px-5 py-3 dark:bg-slate-900"><p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p><p className={`mt-1 text-lg font-semibold tabular ${accent ? "text-emerald-600" : ""}`}>{value}</p></div>; }
function Legend({color, label, dashed}: {color: string; label: string; dashed?: boolean}) { return <span className="flex items-center gap-1.5"><span className="h-0 w-5" style={{borderTop: `2px ${dashed ? "dashed" : "solid"} ${color}`}} />{label}</span>; }
function ForecastTooltip({active, payload, label, currency}: any) { if (!active || !payload?.length) return null; const row = payload[0]?.payload ?? {}; return <div className="rounded-xl border border-slate-200 bg-white/95 p-3 text-xs shadow-xl backdrop-blur dark:border-slate-700 dark:bg-slate-900/95"><p className="mb-2 font-semibold">{formatDate(label)}</p>{row.history != null ? <p>Фактическая цена: <b>{formatMoney(row.history, currency, 2)}</b></p> : null}{row.forecast != null ? <><p>Медиана: <b>{formatMoney(row.forecast, currency, 2)}</b></p><p>50%: {formatMoney(row.q25, currency, 2)} — {formatMoney(row.q75, currency, 2)}</p><p>80%: {formatMoney(row.q10, currency, 2)} — {formatMoney(row.q90, currency, 2)}</p></> : null}</div>; }
function VolumeTooltip({active, payload}: any) { if (!active || !payload?.length || payload[0]?.value == null) return null; return <div className="rounded-lg bg-slate-950 px-2 py-1 text-xs text-white">Объём: {Number(payload[0].value).toLocaleString("ru-RU", {notation: "compact"})}</div>; }
