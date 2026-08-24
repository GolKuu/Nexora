import { api } from "@/services/client";
import type {
  ChangeRecord,
  ChangeSummary,
  InstrumentKind,
  SeriesResponse,
  ScoreHistoryResponse,
} from "@/types/api";

const ROOT: Record<InstrumentKind, string> = { stock: "stocks", bond: "bonds" };

function yearsAgoIso(years: number): string {
  const value = new Date();
  value.setUTCFullYear(value.getUTCFullYear() - years);
  return value.toISOString();
}

/** Charts and the change feed. Both are served from the data the backend
 *  collected itself from public KASE endpoints - no licensed archive. */
export const historyService = {
  series: (kind: InstrumentKind, identifier: string, days = 365) =>
    api.get<SeriesResponse>(
      `/${ROOT[kind]}/${encodeURIComponent(identifier)}/series?days=${days}`,
    ),

  changes: (
    kind: InstrumentKind,
    identifier: string,
    { section, limit = 1000, years = 2 }: { section?: string; limit?: number; years?: number } = {},
  ) => {
    const query = new URLSearchParams({ limit: String(limit), since: yearsAgoIso(years) });
    if (section) query.set("section", section);
    return api.get<ChangeRecord[]>(
      `/${ROOT[kind]}/${encodeURIComponent(identifier)}/changes?${query}`,
    );
  },

  changeSummary: (kind: InstrumentKind, identifier: string, years = 2) =>
    api.get<ChangeSummary>(
      `/${ROOT[kind]}/${encodeURIComponent(identifier)}/change-summary?since=${encodeURIComponent(yearsAgoIso(years))}`,
    ),

  scoreHistory: (identifier: string) =>
    api.get<ScoreHistoryResponse>(`/instruments/${encodeURIComponent(identifier)}/score-history?limit=100`),
};
