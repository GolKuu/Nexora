import { api } from "@/services/client";
import type { CrossAssetCompareResponse, InstrumentSearchResponse, StockCalculation, StockCard, StockListResponse } from "@/types/api";

export const stocksService = {
  list: (limit = 100) => api.get<StockListResponse>(`/stocks?limit=${limit}`),
  top: (category = "best", limit = 12) => api.get<StockListResponse>(`/stocks/top?category=${category}&limit=${limit}`),
  card: (identifier: string) => api.get<StockCard>(`/stocks/${encodeURIComponent(identifier)}`),
  searchAll: (q: string, limit = 20) => api.get<InstrumentSearchResponse>(`/instruments/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  interpretSearch: (query: string, limit = 20) => api.post<{query: string; validated_filters: Record<string, string | number>; assumptions: string[]; items: StockListResponse["items"]; total: number; warning: string}>("/stocks/search", { query, limit }),
  calculate: (identifier: string, amount: number, scenario: string, commission = 0.1) =>
    api.post<StockCalculation>(`/stocks/${encodeURIComponent(identifier)}/investment-calculation`, {
      mode: "amount", amount, currency: "KZT", commission: { type: "percent", value: commission }, scenario, target_period_months: 12,
    }),
  compare: (identifiers: string[], amount?: number) => api.post<{columns: Array<StockCard & {investment_calculation: StockCalculation | null}>; warning: string}>("/stocks/compare", { identifiers, amount, scenario: "base" }),
  compareCrossAsset: (instruments: Array<{identifier: string; instrument_type: "stock" | "bond"}>) =>
    api.post<CrossAssetCompareResponse>("/instruments/compare", { instruments }),
};
