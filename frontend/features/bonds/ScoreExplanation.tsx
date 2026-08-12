"use client";

import useSWR from "swr";

import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Stat";
import { bondsService } from "@/services/bonds";
import { useUiStore } from "@/stores/uiStore";
import { cn } from "@/utils/cn";
import { TONE_TEXT, scoreTone } from "@/utils/score";
import type { ExplanationResponse, ScoreComponent } from "@/types/api";

function ComponentRow({ component }: { component: ScoreComponent }) {
  const tone = scoreTone(component.value);
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="min-w-0 flex-1 truncate text-sm text-slate-600 dark:text-slate-300">
        {component.label}
      </span>
      <span className="w-12 shrink-0 text-right text-xs text-slate-400">
        вес {Math.round(component.weight * 100)}%
      </span>
      <span className={cn("tabular w-10 shrink-0 text-right text-sm font-semibold", TONE_TEXT[tone])}>
        {component.value === null ? "—" : Math.round(component.value)}
      </span>
    </div>
  );
}

export function ScoreExplanation({ ticker }: { ticker: string }) {
  const uiMode = useUiStore((s) => s.uiMode);
  const { data, isLoading } = useSWR<ExplanationResponse>(
    ["explanation", ticker, uiMode],
    () => bondsService.explanation(ticker, uiMode),
    { revalidateOnFocus: false },
  );

  return (
    <Card>
      <CardHeader
        title="Почему такая оценка?"
        action={
          data ? (
            <Badge tone={data.generated_by === "llm" ? "info" : "neutral"}>
              {data.generated_by === "llm" ? "текст: ИИ" : "текст: движок"}
            </Badge>
          ) : null
        }
      />
      <CardBody className="space-y-4">
        {isLoading || !data ? (
          <>
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-4/6" />
          </>
        ) : (
          <>
            <p className="text-sm leading-relaxed text-slate-700 dark:text-slate-200">
              {data.text}
            </p>

            {data.explanation.strengths.length > 0 ? (
              <div>
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-emerald-600">
                  Сильные стороны
                </p>
                <ul className="list-inside list-disc text-sm text-slate-600 dark:text-slate-300">
                  {data.explanation.strengths.map((c) => (
                    <li key={c.code}>{c.label}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {data.explanation.weaknesses.length > 0 ? (
              <div>
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-rose-600">
                  Слабые стороны
                </p>
                <ul className="list-inside list-disc text-sm text-slate-600 dark:text-slate-300">
                  {data.explanation.weaknesses.map((c) => (
                    <li key={c.code}>{c.label}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {data.explanation.missing_data.length > 0 ? (
              <p className="rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                Нет данных по:{" "}
                {data.explanation.missing_data.map((m) => m.label.toLowerCase()).join(", ")}.
                Эти показатели исключены из расчета, а не заменены нулями.
              </p>
            ) : null}

            {uiMode === "pro" ? (
              <div className="border-t border-slate-100 pt-3 dark:border-slate-800">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Составляющие оценки · модель {data.explanation.version}
                </p>
                {data.explanation.components.map((c) => (
                  <ComponentRow key={c.code} component={c} />
                ))}
              </div>
            ) : null}
          </>
        )}
      </CardBody>
    </Card>
  );
}
