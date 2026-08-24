import { api } from "@/services/client";
import type { CrossAssetCompareResponse, DCFLatestResponse, DCFResult, InstrumentSearchResponse, StockCalculation, StockCard, StockEventsResponse, StockForecastPerformanceResponse, StockForecastResponse, StockHistoryResponse, StockListResponse } from "@/types/api";

export const stocksService = {
  list: (limit = 100) => api.get<StockListResponse>(`/stocks?limit=${limit}`),
  top: (category = "best", limit = 12) => api.get<StockListResponse>(`/stocks/top?category=${category}&limit=${limit}`),
  recommend: (amount: number, profile: string, limit = 6) =>
    api.post<{ items: StockListResponse["items"]; amount: number; profile: string; warning: string }>("/stocks/recommend", {
      amount, currency: "KZT", profile, limit,
    }),
  card: (identifier: string) => api.get<StockCard>(`/stocks/${encodeURIComponent(identifier)}`),
  events: (identifier: string) => api.get<StockEventsResponse>(`/stocks/${encodeURIComponent(identifier)}/event-impact`),
  history: (identifier: string) => api.get<StockHistoryResponse>(`/stocks/${encodeURIComponent(identifier)}/history`),
  forecast: (identifier: string, horizon = "20d") => api.get<StockForecastResponse>(`/stocks/${encodeURIComponent(identifier)}/forecast?horizon=${horizon}`),
  forecastPerformance: (identifier: string) => api.get<StockForecastPerformanceResponse>(`/stocks/${encodeURIComponent(identifier)}/forecast-performance`),
  analyzeDcf: (identifier: string, forceRefresh = false) => api.post<DCFResult>(`/stocks/${encodeURIComponent(identifier)}/dcf`, { force_refresh: forceRefresh }),
  latestDcf: (identifier: string) => api.get<DCFLatestResponse>(`/stocks/${encodeURIComponent(identifier)}/dcf-latest`),
  searchAll: (q: string, limit = 20) => api.get<InstrumentSearchResponse>(`/instruments/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  interpretSearch: (query: string, limit = 20) => api.post<{query: string; validated_filters: Record<string, string | number>; assumptions: string[]; items: StockListResponse["items"]; total: number; warning: string}>("/stocks/search", { query, limit }),
  calculate: (identifier: string, input: { mode: "amount" | "quantity"; value: number; scenario: string; commission?: number; commissionType?: "percent" | "fixed" }) =>
    api.post<StockCalculation>(`/stocks/${encodeURIComponent(identifier)}/investment-calculation`, {
      mode: input.mode,
      ...(input.mode === "amount" ? { amount: input.value } : { quantity: input.value }),
      currency: "KZT", commission: { type: input.commissionType ?? "percent", value: input.commission ?? 0.1 }, scenario: input.scenario, target_period_months: 12,
    }),
  compare: (identifiers: string[], amount?: number) => api.post<{columns: Array<StockCard & {investment_calculation: StockCalculation | null; dcf_summary: {status: string; bear_fair_value?: number|null; base_fair_value?: number|null; bull_fair_value?: number|null; base_difference_percent?: number|null; analysis_confidence?: string|null}}> ; warning: string}>("/stocks/compare", { identifiers, amount, scenario: "base" }),
  compareCrossAsset: (instruments: Array<{identifier: string; instrument_type: "stock" | "bond"}>) =>
    api.post<CrossAssetCompareResponse>("/instruments/compare", { instruments }),
};
