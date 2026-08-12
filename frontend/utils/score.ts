/** Presentation rules for 0-100 scores. Thresholds mirror the backend's
 *  verdict ladder in app/scoring/explain.py. */

export type ScoreTone = "excellent" | "good" | "average" | "weak" | "poor" | "unknown";

export function scoreTone(value: number | null | undefined): ScoreTone {
  if (value === null || value === undefined) return "unknown";
  if (value >= 85) return "excellent";
  if (value >= 70) return "good";
  if (value >= 55) return "average";
  if (value >= 40) return "weak";
  return "poor";
}

export const TONE_TEXT: Record<ScoreTone, string> = {
  excellent: "text-emerald-600 dark:text-emerald-400",
  good: "text-teal-600 dark:text-teal-400",
  average: "text-amber-600 dark:text-amber-400",
  weak: "text-orange-600 dark:text-orange-400",
  poor: "text-rose-600 dark:text-rose-400",
  unknown: "text-slate-400 dark:text-slate-500",
};

export const TONE_BG: Record<ScoreTone, string> = {
  excellent: "bg-emerald-500",
  good: "bg-teal-500",
  average: "bg-amber-500",
  weak: "bg-orange-500",
  poor: "bg-rose-500",
  unknown: "bg-slate-300 dark:bg-slate-600",
};

export const TONE_SOFT: Record<ScoreTone, string> = {
  excellent: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  good: "bg-teal-50 text-teal-700 dark:bg-teal-950 dark:text-teal-300",
  average: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  weak: "bg-orange-50 text-orange-700 dark:bg-orange-950 dark:text-orange-300",
  poor: "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
  unknown: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
};

export const BOND_TYPE_LABELS: Record<string, string> = {
  government: "Государственные",
  quasi_sovereign: "Квазигосударственные",
  municipal: "Муниципальные",
  bank: "Банковские",
  corporate: "Корпоративные",
  international: "Международные",
};

export function bondTypeLabel(code: string | null | undefined): string {
  if (!code) return "Прочие";
  return BOND_TYPE_LABELS[code] ?? code;
}
