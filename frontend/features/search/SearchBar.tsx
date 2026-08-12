"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useBondSearch } from "@/hooks/useBonds";
import { formatPercent, formatYears } from "@/utils/format";
import { cn } from "@/utils/cn";

/** Universal search: ticker, ISIN or issuer name. No registration, no filters
 *  to configure first. */
export function SearchBar({ autoFocus = false }: { autoFocus?: boolean }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement>(null);
  const { results, isLoading } = useBondSearch(query);

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  return (
    <div ref={containerRef} className="relative w-full">
      <input
        value={query}
        autoFocus={autoFocus}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && results[0]) {
            router.push(`/bond/${results[0].ticker}`);
            setOpen(false);
          }
          if (e.key === "Escape") setOpen(false);
        }}
        placeholder="Найдите облигацию: тикер, ISIN или название эмитента"
        aria-label="Поиск облигаций"
        className={cn(
          "h-12 w-full rounded-2xl border border-slate-200 bg-white px-4 text-base",
          "shadow-sm placeholder:text-slate-400 focus:border-slate-400 focus:outline-none",
          "dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100",
        )}
      />

      {open && query.trim().length > 0 ? (
        <div className="absolute z-30 mt-2 max-h-96 w-full overflow-auto rounded-2xl border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-900">
          {isLoading && results.length === 0 ? (
            <p className="px-4 py-3 text-sm text-slate-500">Ищем…</p>
          ) : results.length === 0 ? (
            <p className="px-4 py-3 text-sm text-slate-500">
              Ничего не нашлось. Попробуйте тикер или ISIN.
            </p>
          ) : (
            <ul>
              {results.map((bond) => (
                <li key={bond.id}>
                  <Link
                    href={`/bond/${bond.ticker}`}
                    onClick={() => setOpen(false)}
                    className="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">
                        {bond.ticker}
                      </span>
                      <span className="block truncate text-xs text-slate-500">
                        {bond.name}
                      </span>
                    </span>
                    <span className="tabular shrink-0 text-right text-sm">
                      <span className="block font-semibold">
                        {formatPercent(bond.yield_pct)}
                      </span>
                      <span className="block text-xs text-slate-500">
                        {formatYears(bond.years_to_maturity)}
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
