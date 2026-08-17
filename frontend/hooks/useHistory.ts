"use client";

import useSWR from "swr";

import { historyService } from "@/services/history";
import type {
  ChangeRecord,
  ChangeSummary,
  InstrumentKind,
  SeriesResponse,
} from "@/types/api";

/** ``keepPreviousData`` is deliberate: switching the range must not blank the
 *  chart. The panel dims the previous render instead of flashing a skeleton. */
export function useSeries(kind: InstrumentKind, identifier: string | null, days = 365) {
  return useSWR<SeriesResponse>(
    identifier ? ["series", kind, identifier, days] : null,
    () => historyService.series(kind, identifier as string, days),
    { revalidateOnFocus: false, keepPreviousData: true },
  );
}

export function useChanges(
  kind: InstrumentKind,
  identifier: string | null,
  section?: string,
) {
  return useSWR<ChangeRecord[]>(
    identifier ? ["changes", kind, identifier, section ?? ""] : null,
    () => historyService.changes(kind, identifier as string, { section }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );
}

export function useChangeSummary(kind: InstrumentKind, identifier: string | null) {
  return useSWR<ChangeSummary>(
    identifier ? ["change-summary", kind, identifier] : null,
    () => historyService.changeSummary(kind, identifier as string),
    { revalidateOnFocus: false },
  );
}
