"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";

import { bondsService } from "@/services/bonds";
import type {
  BondCard,
  BondListResponse,
  CashFlow,
  HistoryPoint,
  KaseHealth,
  PeersResponse,
} from "@/types/api";

export function useTopBonds(
  limit = 10,
  category?: string,
  excludeGovernment = false,
  minMaturityYears?: number,
) {
  return useSWR<BondListResponse>(
    ["top", limit, category ?? "", excludeGovernment, minMaturityYears ?? ""],
    () => bondsService.top(limit, category, excludeGovernment, minMaturityYears),
    { revalidateOnFocus: false },
  );
}

export function useBondCard(identifier: string | null) {
  return useSWR<BondCard>(
    identifier ? ["bond", identifier] : null,
    () => bondsService.card(identifier as string),
    { revalidateOnFocus: false },
  );
}

export function useCashflows(identifier: string | null) {
  return useSWR<CashFlow[]>(
    identifier ? ["cashflows", identifier] : null,
    () => bondsService.cashflows(identifier as string),
    { revalidateOnFocus: false },
  );
}

export function useHistory(identifier: string | null, days = 180) {
  return useSWR<HistoryPoint[]>(
    identifier ? ["history", identifier, days] : null,
    () => bondsService.history(identifier as string, days),
    { revalidateOnFocus: false },
  );
}

export function usePeers(identifier: string | null) {
  return useSWR<PeersResponse>(
    identifier ? ["peers", identifier] : null,
    () => bondsService.peers(identifier as string),
    { revalidateOnFocus: false },
  );
}

export function useKaseHealth() {
  return useSWR<KaseHealth>("kase-health", () => bondsService.kaseHealth(), {
    revalidateOnFocus: false,
    refreshInterval: 120_000,
  });
}

/** Debounced search; an empty query performs no request at all. */
export function useBondSearch(query: string, delay = 250) {
  const [debounced, setDebounced] = useState(query);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), delay);
    return () => clearTimeout(timer);
  }, [query, delay]);

  const { data, isLoading } = useSWR<BondListResponse>(
    debounced.length >= 1 ? ["search", debounced] : null,
    () => bondsService.search(debounced),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  return { results: data?.items ?? [], isLoading, query: debounced };
}
