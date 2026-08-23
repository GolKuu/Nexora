"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ModeToggle } from "@/components/layout/ModeToggle";
import { cn } from "@/utils/cn";

const NAV = [
  { href: "/", label: "Главная" },
  { href: "/bonds", label: "Облигации" },
  { href: "/stocks", label: "Акции" },
  { href: "/news", label: "Новости" },
  { href: "/compare", label: "Сравнение" },
  { href: "/portfolio", label: "Портфель" },
  { href: "/watchlist", label: "Избранное" },
  { href: "/settings", label: "Настройки" },
  { href: "/system", label: "Система" },
];

export function Header() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/85 backdrop-blur dark:border-slate-800 dark:bg-slate-900/85">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
        <Link href="/" className="shrink-0 text-sm font-semibold tracking-tight">
          KASE&nbsp;Investment&nbsp;AI
        </Link>

        <nav className="hidden min-w-0 flex-1 items-center gap-1 overflow-x-auto sm:flex">
          {NAV.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "shrink-0 rounded-lg px-3 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-slate-100 font-medium text-slate-900 dark:bg-slate-800 dark:text-slate-100"
                    : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <ModeToggle />
      </div>
      <nav className="fixed inset-x-0 bottom-0 z-50 flex h-16 items-center gap-1 overflow-x-auto border-t border-slate-200 bg-white/95 px-2 pb-[env(safe-area-inset-bottom)] backdrop-blur dark:border-slate-800 dark:bg-slate-900/95 sm:hidden">
        {NAV.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return <Link key={item.href} href={item.href} className={cn("shrink-0 rounded-xl px-3 py-2 text-xs", active ? "bg-emerald-50 font-semibold text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200" : "text-slate-500")}>{item.label}</Link>;
        })}
      </nav>
    </header>
  );
}
