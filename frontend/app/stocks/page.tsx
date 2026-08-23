import { SearchBar } from "@/features/search/SearchBar";
import { TopStocks } from "@/features/stocks/TopStocks";
import { StockExplorer } from "@/features/stocks/StockExplorer";
import { NaturalStockSearch } from "@/features/stocks/NaturalStockSearch";

export const metadata = { title: "Акции KASE · KASE Investment AI" };
export default function StocksPage() { return <div className="space-y-5"><div><h1 className="text-2xl font-semibold">Акции KASE</h1><p className="mt-1 text-sm text-slate-500">Качество бизнеса, valuation, дивиденды, ликвидность и риск — без YTM и bond-формул.</p></div><SearchBar /><NaturalStockSearch /><TopStocks /><StockExplorer /></div>; }
