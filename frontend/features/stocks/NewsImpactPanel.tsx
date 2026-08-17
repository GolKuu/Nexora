"use client";

import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useStockEvents, useStockHistory } from "@/hooks/useStocks";
import type { MarketEventItem } from "@/types/api";

const markerTone:Record<string,string>={E:"#8b5cf6",D:"#10b981",P:"#0ea5e9",M:"#f59e0b",R:"#ef4444",N:"#64748b"};
const percent=(value:number|null|undefined)=>value==null?"—":`${value>=0?"+":""}${(value*100).toFixed(1)}%`;
const importance=(value:number)=>value>=.8?"Высокая":value>=.55?"Средняя":"Низкая";

export function NewsImpactPanel({ticker}:{ticker:string}) {
  const events=useStockEvents(ticker); const history=useStockHistory(ticker); const [selected,setSelected]=useState<MarketEventItem|null>(null);
  const chart=useMemo(()=>history.data?.quotes.flatMap(q=>{const price=q.close??q.last;return price==null?[]:[{timestamp:q.timestamp,price}];})??[],[history.data]);
  const markers=useMemo(()=>{if(!chart.length)return [];return (events.data?.items??[]).map(event=>{const target=new Date(event.event_timestamp).getTime();let nearest=chart[0];let distance=Math.abs(new Date(nearest.timestamp).getTime()-target);for(const point of chart){const next=Math.abs(new Date(point.timestamp).getTime()-target);if(next<distance){nearest=point;distance=next;}}return {event,point:nearest};});},[chart,events.data]);
  return <div className="space-y-4">
    <Card><CardHeader title="Цена и события" subtitle="Маркеры привязаны к ближайшей доступной рыночной отметке; при плотных событиях показываются последние релевантные."/><CardBody>
      <div className="h-72" aria-label="График цены с маркерами событий"><ResponsiveContainer width="100%" height="100%"><LineChart data={chart} margin={{top:18,right:16,bottom:8,left:0}}><CartesianGrid strokeDasharray="3 3" opacity={.22}/><XAxis dataKey="timestamp" tickFormatter={v=>new Date(v).toLocaleDateString("ru-RU",{day:"2-digit",month:"short"})} minTickGap={45}/><YAxis domain={["auto","auto"]} width={58}/><Tooltip labelFormatter={v=>new Date(v).toLocaleString("ru-RU")}/><Line type="monotone" dataKey="price" stroke="#0f766e" strokeWidth={2} dot={false}/>{markers.slice(0,12).map(({event,point})=><ReferenceDot key={event.id} x={point.timestamp} y={point.price} r={12} fill={markerTone[event.marker]} stroke="#fff" onClick={()=>setSelected(event)} label={{value:event.marker,fill:"#fff",fontSize:10,fontWeight:700}}/>)}</LineChart></ResponsiveContainer></div>
      {selected?<EventDetails event={selected} onClose={()=>setSelected(null)}/>:null}
    </CardBody></Card>
    <Card><CardHeader title="Что влияет на акцию" subtitle="Фактическая реакция рассчитана из market-data layer. Тональность новости не подменяет движение цены."/><CardBody className="space-y-3">
      {events.isLoading?<p className="text-sm text-slate-500">Загружаем события…</p>:null}{!events.isLoading&&!events.data?.items.length?<p className="text-sm text-slate-500">Связанных обработанных событий пока нет.</p>:null}
      {events.data?.items.slice(0,8).map(event=><button key={event.id} onClick={()=>setSelected(event)} className="w-full rounded-2xl border border-slate-200 p-4 text-left transition hover:border-teal-500 dark:border-slate-700"><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="font-semibold">{event.title}</p><p className="mt-1 text-xs text-slate-500">{event.source} · {new Date(event.event_timestamp).toLocaleString("ru-RU")}</p></div><span className="rounded-full px-2 py-1 text-xs font-bold text-white" style={{background:markerTone[event.marker]}}>{event.marker} · {event.event_type}</span></div><div className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4"><Fact label="Важность" value={importance(event.importance)}/><Fact label="1 день" value={percent(event.reaction?.return_1d)}/><Fact label="Отн. рынка" value={percent(event.reaction?.abnormal_return_1d)}/><Fact label="Объём" value={event.reaction?.volume_ratio==null?"—":`${event.reaction.volume_ratio.toFixed(1)}×`}/></div><p className="mt-3 text-xs text-slate-500">{event.historical_analogs.sufficient_sample?`Рост в ${Math.round((event.historical_analogs.positive_reaction_rate??0)*100)}% из ${event.historical_analogs.count} аналогов`:event.historical_analogs.message}</p></button>)}
    </CardBody></Card>
  </div>;
}
function Fact({label,value}:{label:string;value:string}){return <div><span className="block text-xs text-slate-500">{label}</span><strong className="tabular">{value}</strong></div>}
function EventDetails({event,onClose}:{event:MarketEventItem;onClose:()=>void}){return <div className="mt-4 rounded-2xl bg-slate-950 p-4 text-slate-100"><div className="flex justify-between gap-3"><strong>{event.title}</strong><button onClick={onClose} aria-label="Закрыть">×</button></div><p className="mt-2 text-sm text-slate-300">{event.explanation}</p><div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4"><Fact label="Цена до" value={event.reaction?.price_before?.toLocaleString("ru-RU")??"—"}/><Fact label="30 минут" value={percent(event.reaction?.return_30m)}/><Fact label="5 дней" value={percent(event.reaction?.return_5d)}/><Fact label="Объём" value={event.reaction?.volume_ratio==null?"—":`${event.reaction.volume_ratio.toFixed(1)}×`}/></div><a href={event.source_url} target="_blank" rel="noreferrer" className="mt-4 inline-block text-sm text-teal-300 underline">Открыть новость ↗</a></div>}
