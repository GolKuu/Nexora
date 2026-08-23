import { SystemDashboard } from "@/features/system/SystemDashboard";

export const metadata = { title: "Состояние системы · KASE Investment AI" };

export default function SystemPage() {
  return <div className="space-y-5"><div><h1 className="text-2xl font-semibold">Состояние данных</h1><p className="mt-1 text-sm text-slate-500">KASE, база, постоянный мониторинг, источники, задержка и аномалии парсера.</p></div><SystemDashboard /></div>;
}
