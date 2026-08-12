"use client";

import useSWR from "swr";

import { settingsService } from "@/services/user";
import type { UserSettings } from "@/types/api";

export function useSettings() {
  const { data, error, isLoading, mutate } = useSWR<UserSettings>(
    "/settings",
    () => settingsService.get(),
    { revalidateOnFocus: false },
  );

  async function update(values: Partial<UserSettings>) {
    const optimistic = data ? { ...data, ...values } : undefined;
    await mutate(async () => settingsService.update(values), {
      optimisticData: optimistic,
      rollbackOnError: true,
      revalidate: false,
    });
  }

  return { settings: data, error, isLoading, update, mutate };
}
