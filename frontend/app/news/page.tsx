import { NewsFeed } from "@/features/news/NewsFeed";

export const metadata = { title: "Новости KASE · KASE Investment AI" };

export default function NewsPage() {
  return <div className="space-y-5"><div><h1 className="text-2xl font-semibold">Новости рынка</h1><p className="mt-1 text-sm text-slate-500">События связаны с тикерами, классифицированы и отделены от измеренной реакции цены.</p></div><NewsFeed /></div>;
}
