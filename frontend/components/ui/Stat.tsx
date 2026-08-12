import type { ReactNode } from "react";

import { cn } from "@/utils/cn";

export function Stat({
  label,
  value,
  hint,
  tone,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "positive" | "negative" | "neutral";
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div
        className={cn(
          "tabular mt-1 text-xl font-semibold",
          tone === "positive" && "text-emerald-600 dark:text-emerald-400",
          tone === "negative" && "text-rose-600 dark:text-rose-400",
          (!tone || tone === "neutral") && "text-slate-900 dark:text-slate-100",
        )}
      >
        {value}
      </div>
      {hint ? (
        <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{hint}</div>
      ) : null}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-lg bg-slate-200 dark:bg-slate-800",
        className,
      )}
    />
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <p className="text-base font-medium text-slate-700 dark:text-slate-200">{title}</p>
      {description ? (
        <p className="max-w-md text-sm text-slate-500 dark:text-slate-400">
          {description}
        </p>
      ) : null}
      {action}
    </div>
  );
}
