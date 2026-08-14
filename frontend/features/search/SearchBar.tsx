"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useInstrumentSearch } from "@/hooks/useStocks";
import { cn } from "@/utils/cn";

/** Universal search: ticker, ISIN or issuer name. No registration, no filters
 *  to configure first. */
export function SearchBar({ autoFocus = false }: { autoFocus?: boolean }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement>(null);
  const { results, isLoading } = useInstrumentSearch(query);

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
            router.push(results[0].href);
            setOpen(false);
          }
          if (e.key === "Escape") setOpen(false);
        }}
        placeholder="Тикер, ISIN или компания — акция или облигация"
        aria-label="Поиск инструментов KASE"
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
              {results.map((instrument) => (
                <li key={`${instrument.instrument_type}-${instrument.id}`}>
                  <Link
                    href={instrument.href}
                    onClick={() => setOpen(false)}
                    className="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">
                        {instrument.ticker}
                      </span>
                      <span className="block truncate text-xs text-slate-500">
                        {instrument.name}
                      </span>
                    </span>
                    <span className="shrink-0 rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">{instrument.type_label}</span>
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
