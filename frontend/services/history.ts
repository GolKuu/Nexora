import { api } from "@/services/client";
import type {
  ChangeRecord,
  ChangeSummary,
  InstrumentKind,
  SeriesResponse,
} from "@/types/api";

const ROOT: Record<InstrumentKind, string> = { stock: "stocks", bond: "bonds" };

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
    { section, limit = 200 }: { section?: string; limit?: number } = {},
  ) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (section) query.set("section", section);
    return api.get<ChangeRecord[]>(
      `/${ROOT[kind]}/${encodeURIComponent(identifier)}/changes?${query}`,
    );
  },

  changeSummary: (kind: InstrumentKind, identifier: string) =>
    api.get<ChangeSummary>(
      `/${ROOT[kind]}/${encodeURIComponent(identifier)}/change-summary`,
    ),
};
