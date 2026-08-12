"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { UiMode } from "@/types/api";

interface UiState {
  /** Simple is the default and stays the default: the whole product promise is
   *  that nobody has to configure anything to get value. */
  uiMode: UiMode;
  compareList: string[];
  calculatorAmount: number;
  setUiMode: (mode: UiMode) => void;
  toggleUiMode: () => void;
  toggleCompare: (ticker: string) => void;
  clearCompare: () => void;
  setCalculatorAmount: (amount: number) => void;
}

export const MAX_COMPARE = 5;

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      uiMode: "simple",
      compareList: [],
      calculatorAmount: 1_000_000,
      setUiMode: (uiMode) => set({ uiMode }),
      toggleUiMode: () => set({ uiMode: get().uiMode === "simple" ? "pro" : "simple" }),
      toggleCompare: (ticker) => {
        const current = get().compareList;
        if (current.includes(ticker)) {
          set({ compareList: current.filter((t) => t !== ticker) });
          return;
        }
        if (current.length >= MAX_COMPARE) return;
        set({ compareList: [...current, ticker] });
      },
      clearCompare: () => set({ compareList: [] }),
      setCalculatorAmount: (calculatorAmount) => set({ calculatorAmount }),
    }),
    { name: "kase-bond-ai:ui" },
  ),
);
