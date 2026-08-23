import { SearchBar } from "@/features/search/SearchBar";
import { TopBonds } from "@/features/bonds/TopBonds";
import { BondExplorer } from "@/features/bonds/BondExplorer";

export const metadata = { title: "Облигации KASE · KASE Investment AI" };
export default function BondsPage() { return <div className="space-y-5"><div><h1 className="text-2xl font-semibold">Облигации KASE</h1><p className="mt-1 text-sm text-slate-500">YTM, купоны, погашение, duration и кредитная оценка — существующая bond-модель сохранена.</p></div><SearchBar /><TopBonds /><BondExplorer /></div>; }
