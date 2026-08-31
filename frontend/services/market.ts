import { api } from "@/services/client";
import type { NewsFeedResponse, SubsystemsHealth } from "@/types/api";

export const marketService = {
  news: (params: { limit?: number; eventType?: string; minImportance?: number } = {}) => {
    const query = new URLSearchParams({ limit: String(params.limit ?? 50) });
    if (params.eventType) query.set("event_type", params.eventType);
    if (params.minImportance !== undefined) query.set("min_importance", String(params.minImportance));
    return api.get<NewsFeedResponse>(`/news?${query}`);
  },
  health: () => api.get<Record<string, unknown>>("/health"),
  monitoring: () => api.get<Record<string, unknown>>("/health/monitoring"),
  subsystems: () => api.get<SubsystemsHealth>("/health/subsystems"),
  sources: () => api.get<Record<string, unknown>>("/meta/sources"),
  ingestion: () => api.get<Record<string, unknown>>("/meta/ingestion-metrics"),
};
