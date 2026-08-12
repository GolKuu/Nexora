import { api } from "@/services/client";
import type {
  BondListItem,
  PortfolioDetail,
  UserSettings,
} from "@/types/api";

export const settingsService = {
  get: () => api.get<UserSettings>("/settings"),
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
    api.get<{ items: BondListItem[]; requires_identity: boolean }>("/watchlist"),
  add: (bond: string, note?: string) =>
    api.post<{ id: number; ticker: string; already_present: boolean }>("/watchlist", {
      bond,
      note,
    }),
  remove: (identifier: string) =>
    api.delete<void>(`/watchlist/${encodeURIComponent(identifier)}`),
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
      bond: string;
      quantity: number;
      purchase_clean_price?: number;
      purchase_date?: string;
    },
  ) => api.post<{ id: number; ticker: string }>(`/portfolios/${id}/positions`, payload),
  updatePosition: (id: number, positionId: number, payload: { quantity?: number }) =>
    api.put<{ id: number }>(`/portfolios/${id}/positions/${positionId}`, payload),
  removePosition: (id: number, positionId: number) =>
    api.delete<void>(`/portfolios/${id}/positions/${positionId}`),
};
