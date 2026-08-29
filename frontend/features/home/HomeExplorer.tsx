import { TopBonds } from "@/features/bonds/TopBonds";
import { GoalPlanner } from "@/features/home/GoalPlanner";
import { TopStocks } from "@/features/stocks/TopStocks";

export function HomeExplorer() {
  return <div className="space-y-5">
    <GoalPlanner />
    <TopStocks limit={6} />
    <TopBonds limit={6} />
  </div>;
}
