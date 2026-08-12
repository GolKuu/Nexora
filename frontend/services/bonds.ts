import { api } from "@/services/client";
import type {
  BondCard,
  BondListResponse,
  CalculatorResult,
  CashFlow,
  CompareResponse,
  ExplanationResponse,
  HistoryPoint,
  KaseHealth,
  PeersResponse,
  UiMode,
} from "@/types/api";

export const bondsService = {
  list: (params: Record<string, string | number | undefined> = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") query.set(key, String(value));
    });
    const suffix = query.toString();
    return api.get<BondListResponse>(`/bonds${suffix ? `?${suffix}` : ""}`);
  },

  top: (limit = 10, category?: string) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (category) query.set("category", category);
    return api.get<BondListResponse>(`/bonds/top?${query}`);
  },

  search: (q: string, limit = 20) =>
    api.get<BondListResponse>(
      `/bonds/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),

  card: (identifier: string) =>
    api.get<BondCard>(`/bonds/${encodeURIComponent(identifier)}`),

  cashflows: (identifier: string) =>
    api.get<CashFlow[]>(`/bonds/${encodeURIComponent(identifier)}/cashflows`),

  history: (identifier: string, days = 180) =>
    api.get<HistoryPoint[]>(
      `/bonds/${encodeURIComponent(identifier)}/history?days=${days}`,
    ),

  peers: (identifier: string) =>
    api.get<PeersResponse>(`/bonds/${encodeURIComponent(identifier)}/peers`),

  explanation: (identifier: string, uiMode: UiMode, useAi = true) =>
    api.get<ExplanationResponse>(
      `/bonds/${encodeURIComponent(identifier)}/score-explanation` +
        `?ui_mode=${uiMode}&use_ai=${useAi}`,
    ),

  calculate: (identifier: string, amount: number, reinvest = false) =>
    api.post<CalculatorResult>(`/bonds/${encodeURIComponent(identifier)}/calculate`, {
      amount,
      reinvest_coupons: reinvest,
    }),

  compare: (identifiers: string[], mode: UiMode) =>
    api.post<CompareResponse>("/compare", { identifiers, mode }),

  kaseHealth: () => api.get<KaseHealth>("/health/kase"),
};
