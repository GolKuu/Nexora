import { CompareTable } from "@/features/compare/CompareTable";

export const metadata = { title: "Сравнение · KASE Bond AI" };

export default function ComparePage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Сравнение выпусков</h1>
      <CompareTable />
    </div>
  );
}
