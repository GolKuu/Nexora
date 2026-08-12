import { PortfolioView } from "@/features/portfolio/PortfolioView";

export const metadata = { title: "Портфель · KASE Bond AI" };

export default function PortfolioPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Портфель</h1>
      <PortfolioView />
    </div>
  );
}
