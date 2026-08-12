"use client";

import { useUiStore } from "@/stores/uiStore";
import { cn } from "@/utils/cn";

/** Simple / Pro. Simple is the default everywhere; Pro only ever adds
 *  information, it never changes a number. */
export function ModeToggle() {
  const uiMode = useUiStore((s) => s.uiMode);
  const setUiMode = useUiStore((s) => s.setUiMode);

  return (
    <div
      role="group"
      aria-label="Режим отображения"
      className="flex shrink-0 rounded-xl bg-slate-100 p-0.5 dark:bg-slate-800"
    >
      {(
        [
          ["simple", "Просто"],
          ["pro", "Подробно"],
        ] as const
      ).map(([value, label]) => (
        <button
          key={value}
          type="button"
          onClick={() => setUiMode(value)}
          aria-pressed={uiMode === value}
          className={cn(
            "rounded-[10px] px-3 py-1 text-xs font-medium transition-colors",
            uiMode === value
              ? "bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-slate-100"
              : "text-slate-500 hover:text-slate-700 dark:text-slate-400",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
