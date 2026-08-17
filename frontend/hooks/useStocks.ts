"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { stocksService } from "@/services/stocks";
import type { InstrumentSearchResponse, StockCard, StockEventsResponse, StockForecastResponse, StockHistoryResponse, StockListResponse } from "@/types/api";

export function useTopStocks(category = "best", limit = 12) {
  return useSWR<StockListResponse>(
    ["stock-top", category, limit],
    () => stocksService.top(category, limit),
    {
      revalidateOnFocus: true,
      refreshInterval: 60_000,
      dedupingInterval: 15_000,
    },
  );
}
export function useStockCard(identifier: string) {
  return useSWR<StockCard>(["stock", identifier], () => stocksService.card(identifier), { revalidateOnFocus: false });
}
export function useStockEvents(identifier: string) {
  return useSWR<StockEventsResponse>(["stock-events", identifier], () => stocksService.events(identifier), { revalidateOnFocus: false });
}
export function useStockHistory(identifier: string) {
  return useSWR<StockHistoryResponse>(["stock-history", identifier], () => stocksService.history(identifier), { revalidateOnFocus: false });
}
export function useStockForecast(identifier: string, horizon: string) {
  return useSWR<StockForecastResponse>(["stock-forecast", identifier, horizon], () => stocksService.forecast(identifier, horizon), {
    refreshInterval: 600_000, revalidateOnFocus: true, dedupingInterval: 30_000,
  });
}
export function useInstrumentSearch(query: string, delay = 250) {
  const [debounced, setDebounced] = useState(query);
  useEffect(() => { const timer = setTimeout(() => setDebounced(query.trim()), delay); return () => clearTimeout(timer); }, [query, delay]);
  const result = useSWR<InstrumentSearchResponse>(debounced ? ["instrument-search", debounced] : null, () => stocksService.searchAll(debounced), { keepPreviousData: true, revalidateOnFocus: false });
  return { results: result.data?.items ?? [], isLoading: result.isLoading };
}
