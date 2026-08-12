import type { Metadata } from "next";

import { DataModeBanner } from "@/components/layout/DataModeBanner";
import { Header } from "@/components/layout/Header";

import "./globals.css";

export const metadata: Metadata = {
  title: "KASE Bond AI — простой анализ облигаций KASE",
  description:
    "Доходность, доход после инфляции, надежность и ликвидность облигаций KASE " +
    "простым языком. Все расчеты выполняет детерминированный движок.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>
        <Header />
        <DataModeBanner />
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
        <footer className="mx-auto max-w-6xl px-4 pb-10 pt-4 text-xs text-slate-400">
          KASE Bond AI — аналитический сервис. Не является инвестиционной
          рекомендацией. Все расчеты выполняются детерминированным движком;
          языковая модель используется только для объяснений.
        </footer>
      </body>
    </html>
  );
}
