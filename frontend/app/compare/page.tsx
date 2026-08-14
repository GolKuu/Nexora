import { CompareTable } from "@/features/compare/CompareTable";
import { StockCompare } from "@/features/compare/StockCompare";

export const metadata = { title: "Сравнение · KASE Bond AI" };

export default function ComparePage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Сравнение инструментов</h1>
      <StockCompare />
      <h2 className="pt-4 text-xl font-semibold">Облигации</h2>
      <CompareTable />
    </div>
  );
}
