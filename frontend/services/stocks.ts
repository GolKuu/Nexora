import { api } from "@/services/client";
import type { InstrumentSearchResponse, StockCalculation, StockCard, StockListResponse } from "@/types/api";

export const stocksService = {
  list: (limit = 100) => api.get<StockListResponse>(`/stocks?limit=${limit}`),
  top: (category = "best", limit = 12) => api.get<StockListResponse>(`/stocks/top?category=${category}&limit=${limit}`),
  card: (identifier: string) => api.get<StockCard>(`/stocks/${encodeURIComponent(identifier)}`),
  searchAll: (q: string, limit = 20) => api.get<InstrumentSearchResponse>(`/instruments/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  calculate: (identifier: string, amount: number, scenario: string, commission = 0.1) =>
    api.post<StockCalculation>(`/stocks/${encodeURIComponent(identifier)}/investment-calculation`, {
      mode: "amount", amount, currency: "KZT", commission: { type: "percent", value: commission }, scenario, target_period_months: 12,
    }),
  compare: (identifiers: string[], amount?: number) => api.post<{columns: Array<StockCard & {investment_calculation: StockCalculation | null}>; warning: string}>("/stocks/compare", { identifiers, amount, scenario: "base" }),
};
