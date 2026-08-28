"use client";

import { useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { EmptyState, Skeleton } from "@/components/ui/Stat";
import { useTechnicalAnalysis, useTechnicalSeries } from "@/hooks/useStocks";
import { useUiStore } from "@/stores/uiStore";
import type { TechnicalAnalysisResponse, TechnicalLevel } from "@/types/api";
import { formatCompact, formatMoney, formatNumber } from "@/utils/format";

const RANGE_OPTIONS = [["1М","1m"],["3М","3m"],["6М","6m"],["1Г","1y"],["2Г","2y"],["MAX","max"]] as const;
const OVERLAYS = ["sma20","sma50","sma200","ema20","ema50","bollinger","support","resistance","fibonacci"] as const;
const COLORS:Record<string,string> = {sma20:"#38bdf8",sma50:"#8b5cf6",sma200:"#f59e0b",ema20:"#22c55e",ema50:"#ec4899"};
const GRID = {stroke:"var(--viz-grid)",strokeWidth:1};
const AXIS = {fontSize:11,fill:"var(--viz-ink-muted)"};

const TREND_LABELS:Record<string,string> = {
  STRONG_UPTREND:"Сильный восходящий", UPTREND:"Восходящий", MIXED:"Смешанный",
  DOWNTREND:"Нисходящий", STRONG_DOWNTREND:"Сильный нисходящий",
};
const RISK_LABELS:Record<string,string> = {LOW:"Низкий",MODERATE:"Умеренный",ELEVATED:"Повышенный",HIGH:"Высокий"};
const RSI_LABELS:Record<string,string> = {OVERSOLD:"зона перепроданности",WEAK:"слабый импульс",NEUTRAL:"нейтрально",POSITIVE_MOMENTUM:"положительный импульс",OVERBOUGHT:"зона перекупленности"};
const VOLUME_LABELS:Record<string,string> = {CONFIRMED:"Движение подтверждается повышенным объёмом",WEAK:"Объём ниже среднего — подтверждение слабое",NEUTRAL:"Объём без выраженного подтверждения",UNAVAILABLE:"Нет фактических данных объёма"};

function zone(level?: TechnicalLevel): string {
  return level ? `${formatNumber(level.level_low,2)}–${formatNumber(level.level_high,2)}` : "Недостаточно данных";
}

function StatusValue({value,status}:{value:string;status?:string}) {
  return <span className={status && status!=="READY"?"text-slate-500":"font-semibold"}>{status && status!=="READY"?"Недостаточно данных":value}</span>;
}

export function TechnicalAnalysisPanel({ticker,currency}:{ticker:string;currency:string}) {
  const uiMode=useUiStore(state=>state.uiMode);
  const {data,isLoading,error}=useTechnicalAnalysis(ticker);
  const [range,setRange]=useState("1y");
  const [overlays,setOverlays]=useState<Set<string>>(new Set(["sma50","sma200","support","resistance"]));
  const [technicalMarkers,setTechnicalMarkers]=useState(true);
  const requested=useMemo(()=>Array.from(overlays).filter(item=>!(["support","resistance","fibonacci"].includes(item))),[overlays]);
  const proIndicators=uiMode==="pro"?["rsi","macd","volume","obv","atr"]:[];
  const seriesIndicators=useMemo(()=>Array.from(new Set([...requested,...proIndicators])),[requested,uiMode]); // eslint-disable-line react-hooks/exhaustive-deps
  const series=useTechnicalSeries(ticker,range,seriesIndicators);

  if(isLoading&&!data)return <Card><CardHeader title="Технический анализ"/><CardBody><Skeleton className="h-72 w-full"/></CardBody></Card>;
  if(error||!data||!data.last_trade)return <Card><CardHeader title="Технический анализ"/><CardBody><EmptyState title="Технический анализ пока недоступен" description="Нужна фактическая история сделок KASE; отсутствующие сессии не создаются искусственно."/></CardBody></Card>;

  const support=data.levels.support[0], resistance=data.levels.resistance[0];
  const volatility=data.atr.percent==null?"Недоступна":data.atr.percent<2?"Низкая":data.atr.percent<4?"Средняя":"Повышенная";
  const lowConfidence=data.data_quality.technical_confidence==="LOW";
  return <section className="space-y-3" aria-labelledby="technical-analysis-title">
    <Card>
      <CardHeader title="ТЕХНИЧЕСКИЙ АНАЛИЗ" subtitle={`Фактические торговые сессии KASE · ${data.data_quality.observations} наблюдений · на ${data.last_trade.trading_date}`} action={<div className="flex gap-2"><Badge tone={lowConfidence?"warning":"success"}>Надёжность: {data.data_quality.technical_confidence}</Badge><Badge tone="neutral">Отдельно от Investment Score</Badge></div>}/>
      <CardBody className="space-y-5">
        {lowConfidence?<div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">Технический сигнал имеет низкую надёжность из-за редких сделок. {data.data_quality.liquidity.reasons.join(" ")}</div>:null}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="Тренд" help="Положение цены относительно средних, их наклон и MACD — RSI отдельно тренд не определяет." value={TREND_LABELS[data.trend.state]??data.trend.state}/>
          <Metric label="Поддержка" help="Зона, где ранее появлялся спрос и падение замедлялось; разворот не гарантирован." value={`${zone(support)} ${support?currency:""}`}/>
          <Metric label="Сопротивление" help="Зона, где ранее усиливались продажи; пробой или разворот не гарантирован." value={`${zone(resistance)} ${resistance?currency:""}`}/>
          <Metric label="RSI14" help="Сила недавнего движения от 0 до 100; экстремум не является командой купить или продать." value={data.rsi.value==null?"Недостаточно данных":`${formatNumber(data.rsi.value,1)} — ${RSI_LABELS[data.rsi.zone??""]??data.rsi.zone}`}/>
          <Metric label="Объём" help="Сравнение последнего фактического объёма со средним за 20 торговых сессий." value={VOLUME_LABELS[data.volume.confirmation]??data.volume.confirmation}/>
          <Metric label="Волатильность" help="ATR показывает типичный размер движения, но не его направление." value={data.atr.percent==null?volatility:`${volatility} · ATR ${formatNumber(data.atr.percent,1)}%`}/>
          <Metric label="Технический риск" help="Отдельная оценка структуры, волатильности и ликвидности; фундаментальный риск не меняется." value={RISK_LABELS[data.technical_risk.label]??data.technical_risk.label}/>
          <Metric label="Технический импульс" help="Отдельный технический показатель 0–100, не Investment Score." value={`${data.technical_momentum_score.value}/100`}/>
        </div>
        <div><p className="text-sm font-semibold">Почему?</p><ul className="mt-2 space-y-1 text-sm text-slate-600 dark:text-slate-300">{data.explanation.map((item,index)=><li key={index}>• {item}</li>)}</ul></div>
      </CardBody>
    </Card>

    <TechnicalChart data={data} series={series.data?.series??[]} isLoading={series.isLoading} range={range} setRange={setRange} overlays={overlays} setOverlays={setOverlays} markers={technicalMarkers} setMarkers={setTechnicalMarkers} pro={uiMode==="pro"} currency={currency}/>

    {uiMode==="pro"?<ProDetails data={data} series={series.data?.series??[]} currency={currency}/>:null}
    <p className="px-1 text-xs leading-5 text-slate-500">{data.disclaimer} Расчёты используют только фактические торговые записи; дни без сделок не интерполируются.</p>
  </section>;
}

function Metric({label,value,help}:{label:string;value:string;help:string}) {
  return <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800/70"><div className="flex items-center gap-1"><p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p><span title={help} aria-label={`Что это? ${help}`} className="cursor-help text-xs text-slate-400">ⓘ</span></div><p className="mt-1 text-sm font-semibold">{value}</p></div>;
}

function TechnicalChart({data,series,isLoading,range,setRange,overlays,setOverlays,markers,setMarkers,pro,currency}:{data:TechnicalAnalysisResponse;series:Array<Record<string,number|string|null>>;isLoading:boolean;range:string;setRange:(v:string)=>void;overlays:Set<string>;setOverlays:(v:Set<string>)=>void;markers:boolean;setMarkers:(v:boolean)=>void;pro:boolean;currency:string}) {
  const toggle=(key:string)=>{const next=new Set(overlays);next.has(key)?next.delete(key):next.add(key);setOverlays(next)};
  return <Card><CardHeader title="Цена и технические уровни" subtitle="Каждая линия отключается; по умолчанию показаны SMA50/SMA200 и подтверждённые зоны."/><CardBody className="space-y-4">
    <div className="flex flex-wrap gap-2">
      <div className="flex rounded-xl border border-slate-200 p-1 dark:border-slate-700">{RANGE_OPTIONS.map(([label,value])=><button key={value} onClick={()=>setRange(value)} aria-pressed={range===value} className={range===value?"rounded-lg bg-slate-900 px-3 py-1 text-xs text-white dark:bg-white dark:text-slate-900":"rounded-lg px-3 py-1 text-xs"}>{label}</button>)}</div>
      {(pro?OVERLAYS:OVERLAYS.filter(item=>["sma50","sma200","support","resistance"].includes(item))).map(item=><button key={item} onClick={()=>toggle(item)} aria-pressed={overlays.has(item)} className={overlays.has(item)?"rounded-lg border border-slate-900 bg-slate-900 px-2 py-1 text-xs text-white dark:border-white dark:bg-white dark:text-slate-900":"rounded-lg border border-slate-200 px-2 py-1 text-xs dark:border-slate-700"}>{item.toUpperCase()}</button>)}
      <button onClick={()=>setMarkers(!markers)} aria-pressed={markers} className={markers?"rounded-lg border border-indigo-500 bg-indigo-50 px-2 py-1 text-xs text-indigo-700 dark:bg-indigo-950":"rounded-lg border border-slate-200 px-2 py-1 text-xs dark:border-slate-700"}>GC/DC/BO/BD/DIV</button>
    </div>
    <div className={isLoading?"h-80 opacity-50":"h-80"}><ResponsiveContainer width="100%" height="100%"><LineChart data={series} margin={{top:15,right:16,left:4,bottom:4}} accessibilityLayer><CartesianGrid {...GRID} vertical={false}/><XAxis dataKey="date" tick={AXIS} minTickGap={28}/><YAxis tick={AXIS} width={64} domain={["auto","auto"]}/><Tooltip formatter={(value)=>typeof value==="number"?formatMoney(value,currency,2):String(value)}/>
      {overlays.has("support")&&data.levels.support.map((level,index)=><ReferenceArea key={`s${index}`} y1={level.level_low} y2={level.level_high} fill="#22c55e" fillOpacity={0.10} stroke="#22c55e" strokeOpacity={0.45}/>) }
      {overlays.has("resistance")&&data.levels.resistance.map((level,index)=><ReferenceArea key={`r${index}`} y1={level.level_low} y2={level.level_high} fill="#ef4444" fillOpacity={0.08} stroke="#ef4444" strokeOpacity={0.4}/>) }
      {overlays.has("fibonacci")&&data.fibonacci.levels.map(level=><ReferenceArea key={level.ratio} y1={level.level_low} y2={level.level_high} fill="#64748b" fillOpacity={0.04} stroke="#64748b" strokeDasharray="3 3"/>)}
      {markers&&data.signals.filter(signal=>signal.timestamp).map((signal,index)=>{const labels:Record<string,string>={GOLDEN_CROSS:"GC",DEATH_CROSS:"DC",BREAKOUT:"BO",BREAKDOWN:"BD",BULLISH_DIVERGENCE:"DIV",BEARISH_DIVERGENCE:"DIV"};const label=labels[signal.type];return label?<ReferenceLine key={`${signal.type}-${signal.timestamp}-${index}`} x={(signal.timestamp as string).slice(0,10)} stroke={signal.type.includes("GOLDEN")||signal.type.includes("BULLISH")||signal.type==="BREAKOUT"?"#22c55e":"#ef4444"} strokeDasharray="4 3" label={{value:label,fontSize:10}}/>:null})}
      <Line type="monotone" dataKey="price" name="Цена" stroke="var(--viz-series-1)" strokeWidth={2.5} dot={false}/>{Array.from(overlays).filter(key=>COLORS[key]).map(key=><Line key={key} type="monotone" dataKey={key} name={key.toUpperCase()} stroke={COLORS[key]} strokeWidth={1.4} dot={false} connectNulls={false}/>) }
      {overlays.has("bollinger")?<><Line type="monotone" dataKey="bollinger_upper" name="Bollinger верх" stroke="#94a3b8" strokeDasharray="4 3" dot={false}/><Line type="monotone" dataKey="bollinger_lower" name="Bollinger низ" stroke="#94a3b8" strokeDasharray="4 3" dot={false}/></>:null}
    </LineChart></ResponsiveContainer></div>
  </CardBody></Card>;
}

function ProDetails({data,series,currency}:{data:TechnicalAnalysisResponse;series:Array<Record<string,number|string|null>>;currency:string}) {
  return <div className="space-y-3"><Card><CardHeader title="Pro: значения и происхождение" subtitle={`Конфигурация ${data.data_quality.config_version} · ${data.data_quality.first_trade_date} — ${data.data_quality.last_trade_date}`}/><CardBody className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
    {["sma20","sma50","sma200","ema12","ema20","ema26","ema50","ema200"].map(key=><Metric key={key} label={key.toUpperCase()} help="Средняя по фактическим торговым наблюдениям; пропущенные дни не добавляются." value={data.moving_averages[key]?.value==null?"Недостаточно истории":formatMoney(data.moving_averages[key].value,currency,2)}/>) }
    <Metric label="MACD" help="EMA12 минус EMA26; signal — EMA9 линии MACD." value={data.macd.macd==null?"Недостаточно истории":`${formatNumber(data.macd.macd,3)} / signal ${formatNumber(data.macd.signal,3)}`}/>
    <Metric label="Bollinger" help="SMA20 ± 2 стандартных отклонения; касание полосы не является командой." value={data.bollinger.status!=="READY"?"Недостаточно истории":`${data.bollinger.state} · %B ${formatNumber(data.bollinger.percent_b,2)}`}/>
    <Metric label="OBV" help="Направление накопленного фактического объёма важнее абсолютного значения." value={data.obv.status!=="READY"?"Нет данных объёма":`${data.obv.trend} · ${formatCompact(data.obv.value)}`}/>
    <Metric label="ATR14" help="Типичный размер движения, не направление." value={data.atr.value==null?"Нет фактического OHLC":`${formatMoney(data.atr.value,currency,2)} / ${formatNumber(data.atr.percent,2)}%`}/>
    <Metric label="Дивергенция RSI" help="Подтверждённые локальные экстремумы с минимальным расстоянием; однодневный шум исключён." value={data.rsi.divergence.state}/>
    <Metric label="Confluence" help="Совпадение независимых технических сигналов с явным показом конфликтов." value={`${data.confluence.confluence_score}/100 · ${data.confluence.state}`}/>
  </CardBody></Card>
  <div className="grid gap-3 lg:grid-cols-2"><IndicatorPlot title="RSI (отдельная шкала 0–100)" data={series} keys={["rsi"]} domain={[0,100]}/><IndicatorPlot title="MACD" data={series} keys={["macd","macd_signal","macd_histogram"]}/><VolumePlot data={series}/><IndicatorPlot title="ATR" data={series} keys={["atr"]}/><IndicatorPlot title="OBV" data={series} keys={["obv"]}/></div>
  </div>;
}

function IndicatorPlot({title,data,keys,domain}:{title:string;data:Array<Record<string,number|string|null>>;keys:string[];domain?:[number,number]}) {return <Card><CardHeader title={title}/><CardBody><div className="h-48"><ResponsiveContainer width="100%" height="100%"><LineChart data={data}><CartesianGrid {...GRID} vertical={false}/><XAxis dataKey="date" tick={AXIS} minTickGap={30}/><YAxis tick={AXIS} width={45} domain={domain}/><Tooltip/>{keys.map((key,index)=><Line key={key} type="monotone" dataKey={key} stroke={["#6366f1","#f59e0b","#94a3b8"][index%3]} dot={false} connectNulls={false}/>)}</LineChart></ResponsiveContainer></div></CardBody></Card>}
function VolumePlot({data}:{data:Array<Record<string,number|string|null>>}) {return <Card><CardHeader title="Объём"/><CardBody><div className="h-48"><ResponsiveContainer width="100%" height="100%"><BarChart data={data}><CartesianGrid {...GRID} vertical={false}/><XAxis dataKey="date" tick={AXIS} minTickGap={30}/><YAxis tick={AXIS} width={55} tickFormatter={formatCompact}/><Tooltip/><Bar dataKey="volume" fill="#0ea5e9" maxBarSize={18}/></BarChart></ResponsiveContainer></div></CardBody></Card>}
