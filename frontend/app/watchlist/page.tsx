import { WatchlistView } from "@/features/watchlist/WatchlistView";

export const metadata = { title: "Избранное · KASE Bond AI" };

export default function WatchlistPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Избранное</h1>
      <WatchlistView />
    </div>
  );
}
