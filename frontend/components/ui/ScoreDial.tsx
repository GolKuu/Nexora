import { cn } from "@/utils/cn";
import { TONE_TEXT, scoreTone } from "@/utils/score";

/** A single 0-100 score, drawn as a ring. Unknown scores show a dash rather
 *  than an empty ring, so "no data" never reads as "zero". */
export function ScoreDial({
  value,
  label,
  size = 96,
  caption,
}: {
  value: number | null | undefined;
  label?: string;
  size?: number;
  caption?: string;
}) {
  const tone = scoreTone(value);
  const stroke = size <= 64 ? 6 : 8;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = value === null || value === undefined ? 0 : Math.max(0, Math.min(100, value));
  const offset = circumference * (1 - pct / 100);

  const strokeColor =
    tone === "excellent"
      ? "#10b981"
      : tone === "good"
        ? "#14b8a6"
        : tone === "average"
          ? "#f59e0b"
          : tone === "weak"
            ? "#f97316"
            : tone === "poor"
              ? "#f43f5e"
              : "#cbd5e1";

  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            strokeWidth={stroke}
            className="stroke-slate-150 dark:stroke-slate-800"
            stroke="currentColor"
            opacity={0.15}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={strokeColor}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span
            className={cn(
              "tabular font-semibold",
              size > 80 ? "text-2xl" : "text-lg",
              TONE_TEXT[tone],
            )}
          >
            {value === null || value === undefined ? "—" : Math.round(value)}
          </span>
          {label ? (
            <span className="text-[10px] uppercase tracking-wide text-slate-400">
              {label}
            </span>
          ) : null}
        </div>
      </div>
      {caption ? (
        <span className="text-center text-xs text-slate-500 dark:text-slate-400">
          {caption}
        </span>
      ) : null}
    </div>
  );
}

/** Compact horizontal variant used inside lists. */
export function ScoreBar({
  value,
  label,
  hint,
}: {
  value: number | null | undefined;
  label: string;
  hint?: string | null;
}) {
  const tone = scoreTone(value);
  const width = value === null || value === undefined ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm text-slate-600 dark:text-slate-300">{label}</span>
        <span className={cn("tabular text-sm font-semibold", TONE_TEXT[tone])}>
          {value === null || value === undefined ? "нет данных" : Math.round(value)}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            tone === "excellent" && "bg-emerald-500",
            tone === "good" && "bg-teal-500",
            tone === "average" && "bg-amber-500",
            tone === "weak" && "bg-orange-500",
            tone === "poor" && "bg-rose-500",
            tone === "unknown" && "bg-slate-300 dark:bg-slate-700",
          )}
          style={{ width: `${width}%` }}
        />
      </div>
      {hint ? (
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p>
      ) : null}
    </div>
  );
}
