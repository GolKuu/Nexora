"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { stocksService } from "@/services/stocks";
import type { InstrumentSearchResponse, StockCard, StockListResponse } from "@/types/api";

export function useTopStocks(category = "best", limit = 12) {
  return useSWR<StockListResponse>(["stock-top", category, limit], () => stocksService.top(category, limit), { revalidateOnFocus: false });
}
export function useStockCard(identifier: string) {
  return useSWR<StockCard>(["stock", identifier], () => stocksService.card(identifier), { revalidateOnFocus: false });
}
export function useInstrumentSearch(query: string, delay = 250) {
  const [debounced, setDebounced] = useState(query);
  useEffect(() => { const timer = setTimeout(() => setDebounced(query.trim()), delay); return () => clearTimeout(timer); }, [query, delay]);
  const result = useSWR<InstrumentSearchResponse>(debounced ? ["instrument-search", debounced] : null, () => stocksService.searchAll(debounced), { keepPreviousData: true, revalidateOnFocus: false });
  return { results: result.data?.items ?? [], isLoading: result.isLoading };
}
