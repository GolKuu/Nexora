import { api } from "@/services/client";
import type {
  BondCard,
  BondInvestmentCalculation,
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

  top: (
    limit = 10,
    category?: string,
    excludeGovernment = false,
    minMaturityYears?: number,
  ) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (category) query.set("category", category);
    if (excludeGovernment) query.set("exclude_government", "true");
    if (minMaturityYears !== undefined) {
      query.set("min_maturity_years", String(minMaturityYears));
    }
    return api.get<BondListResponse>(`/bonds/top?${query}`);
  },

  recommend: (amount: number, profile: "conservative" | "balanced" | "aggressive", limit = 6) =>
    api.post<{ items: Array<{ ticker: string; issuer: string | null; currency: string; maturity_date: string | null; ytm_pct: number | null; investment_score: number | null; reason_codes: string[] }>; amount: number; profile: string; warnings: string[] }>("/bonds/recommend", {
      amount, currency: "KZT", profile, limit, inflation_enabled: true,
    }),

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

  calculateInvestment: (identifier: string, input: { mode: "amount" | "quantity"; value: number; commission: number; commissionType: "percent" | "fixed"; inflationEnabled: boolean; exitMode: "maturity" | "date"; exitDate?: string; scenario: "bad" | "base" | "good" }) =>
    api.post<BondInvestmentCalculation>(`/bonds/${encodeURIComponent(identifier)}/investment-calculation`, {
      mode: input.mode,
      ...(input.mode === "amount" ? { amount: input.value } : { quantity: input.value }),
      currency: "KZT", commission: { type: input.commissionType, value: input.commission },
      inflation_enabled: input.inflationEnabled, exit_mode: input.exitMode,
      exit_date: input.exitMode === "date" ? input.exitDate : undefined,
      scenario: input.scenario,
    }),

  compare: (identifiers: string[], mode: UiMode, amount?: number) =>
    api.post<CompareResponse>("/compare", { identifiers, mode, amount, inflation_enabled: true }),

  kaseHealth: () => api.get<KaseHealth>("/health/kase"),
};
