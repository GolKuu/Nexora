import { api } from "@/services/client";
import type {
  BondListItem,
  DCFResult,
  GoalPlan,
  PortfolioDetail,
  StockListItem,
  TechnicalAnalysisResponse,
  UserSettings,
} from "@/types/api";

export const settingsService = {
  get: () => api.get<UserSettings>("/settings"),
  dcfUsage: () => api.get<DCFResult["usage"]>("/me/dcf-usage"),
  dcfHealth: () => api.get<{engine:{status:string;version:string;deterministic:boolean};financial_data:{statements:number};macro_provider:{status:string};ai_explanation:{status:string}}>("/health/dcf"),
  monitoringHealth: () => api.get<{status?:string;state?:string;last_success_at?:string|null;detail?:string|null}>("/health/monitoring"),
  update: (values: Partial<UserSettings>) => api.put<UserSettings>("/settings", values),
  inflation: (horizonYears?: number) =>
    api.get<{
      enabled: boolean;
      rate: number | null;
      rate_pct?: number;
      source: string | null;
      kind?: string;
      period_end?: string | null;
      note?: string | null;
    }>(`/settings/inflation${horizonYears ? `?horizon_years=${horizonYears}` : ""}`),
};

export const watchlistService = {
  list: () =>
    api.get<{ items: Array<(BondListItem & { instrument_type: "bond" }) | (StockListItem & {technical_summary?:Pick<TechnicalAnalysisResponse,"trend"|"rsi"|"technical_risk"|"technical_momentum_score"|"as_of">})>; requires_identity: boolean }>("/watchlist"),
  add: (identifier: string, instrumentType: "bond" | "stock" = "bond", note?: string) =>
    api.post<{ id: number; ticker: string; already_present: boolean }>("/watchlist", {
      [instrumentType]: identifier,
      instrument_type: instrumentType,
      note,
    }),
  remove: (identifier: string, instrumentType: "bond" | "stock" = "bond") =>
    api.delete<void>(`/watchlist/${encodeURIComponent(identifier)}?instrument_type=${instrumentType}`),
};

export interface UserAlert { id: number; ticker: string; instrument_type: "bond" | "stock"; kind: string; threshold: number | null; is_active: boolean; last_triggered_at: string | null; message: string | null }
export const alertsService = {
  list: () => api.get<{items: UserAlert[]; requires_identity: boolean}>("/alerts"),
  addStock: (stock: string, kind: string, threshold?: number) => api.post<UserAlert>("/alerts", { stock, instrument_type: "stock", kind, threshold }),
  update: (id: number, is_active: boolean) => api.put<UserAlert>(`/alerts/${id}`, { is_active }),
  remove: (id: number) => api.delete<void>(`/alerts/${id}`),
};

export const portfolioService = {
  list: () =>
    api.get<{
      items: { id: number; name: string; base_currency: string; position_count: number }[];
      requires_identity: boolean;
    }>("/portfolios"),
  create: (name: string) => api.post<{ id: number; name: string }>("/portfolios", { name }),
  detail: (id: number) => api.get<PortfolioDetail>(`/portfolios/${id}`),
  addPosition: (
    id: number,
    payload: {
      bond?: string;
      stock?: string;
      instrument_type: "bond" | "stock";
      quantity: number;
      purchase_clean_price?: number;
      purchase_price?: number;
      purchase_date?: string;
      fees?: number;
    },
  ) => api.post<{ id: number; ticker: string }>(`/portfolios/${id}/positions`, payload),
  updatePosition: (id: number, positionId: number, payload: {
    quantity?: number;
    purchase_clean_price?: number;
    purchase_price?: number;
    purchase_date?: string;
    fees?: number;
    note?: string;
  }) =>
    api.put<{ id: number }>(`/portfolios/${id}/positions/${positionId}`, payload),
  removePosition: (id: number, positionId: number) =>
    api.delete<void>(`/portfolios/${id}/positions/${positionId}`),
};

export interface GoalPlanInput {
  starting_capital: number;
  target_type: "FINAL_VALUE" | "PROFIT";
  target_amount: number;
  horizon_months: number;
  monthly_contribution: number;
  risk_profile: "conservative" | "balanced" | "growth" | "income";
  currency: "KZT";
  excluded_instruments?: string[];
}

export const goalPlannerService = {
  plan: (payload: GoalPlanInput) => api.post<GoalPlan>("/investment-goals/plan", payload),
  copyToPortfolio: (goalId: number) => api.post<{ portfolio_id: number; positions_added: number; already_copied: boolean; status: "PLANNED" }>(`/investment-goals/${goalId}/copy-to-portfolio`),
  replan: (goalId: number) => api.post<GoalPlan>(`/investment-goals/${goalId}/replan`),
  edit: (goalId: number, positions: Array<{ticker:string;quantity:number}>) => api.put<GoalPlan>(`/investment-goals/${goalId}/plan`, {positions}),
  markExecuted: (goalId: number, positionId: number, payload: { actual_quantity: number; actual_price: number; actual_commission: number; execution_date: string }) =>
    api.post(`/investment-goals/${goalId}/positions/${positionId}/mark-executed`, payload),
};
