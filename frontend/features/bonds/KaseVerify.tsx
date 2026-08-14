"use client";

/** "Проверить на KASE" and "Открыть на KASE" (§50, §51).
 *
 *  Deliberately says nothing about browsers, selectors or automation. The user
 *  gets a source, a time and, when something went wrong, a plain-language
 *  reason - never "Playwright clicked a selector".
 */

import { useState } from "react";
import { mutate } from "swr";

import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { ApiError } from "@/services/client";
import { browserService } from "@/services/browser";
import type { KaseAnalysisResponse, KaseVerifyResponse } from "@/types/api";

interface Props {
  ticker: string;
  kaseUrl?: string | null;
}

export function KaseVerify({ ticker, kaseUrl }: Props) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<KaseVerifyResponse | null>(null);
  const [analysis, setAnalysis] = useState<KaseAnalysisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function verify() {
    // One refresh at a time; the backend also de-duplicates concurrent calls.
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const payload = await browserService.verify(ticker);
      setResult(payload);
      await mutate(`bond:${ticker}`);
    } catch (exc) {
      setError(
        exc instanceof ApiError
          ? exc.message
          : "Не удалось проверить выпуск на KASE.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function analyze() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      setAnalysis(await browserService.analyze(ticker));
    } catch (exc) {
      setError(
        exc instanceof ApiError
          ? exc.message
          : "Не удалось выполнить анализ страницы KASE.",
      );
    } finally {
      setBusy(false);
    }
  }

  const fieldCount = result ? Object.keys(result.fields ?? {}).length : 0;

  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => void verify()} disabled={busy}>
            {busy ? "Проверяем…" : "Проверить на KASE"}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void analyze()}
            disabled={busy}
          >
            {busy ? "Читаем KASE…" : "Анализировать на KASE"}
          </Button>
          {kaseUrl ? (
            <a
              href={kaseUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex h-8 items-center rounded-xl border border-slate-200 px-3 text-sm text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              Открыть на KASE ↗
            </a>
          ) : null}
        </div>

        {error ? (
          <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>
        ) : null}

        {result ? (
          <div className="space-y-1 text-sm">
            <p className="text-slate-700 dark:text-slate-200">
              Источник: {result.source}
            </p>
            <p className="text-slate-500 dark:text-slate-400">
              {result.ok
                ? result.checked_at_label
                : "Последние проверенные данные"}
            </p>
            {result.notice ? (
              <p className="rounded-lg bg-amber-50 px-2 py-1 text-amber-900 dark:bg-amber-950 dark:text-amber-200">
                {result.notice}
              </p>
            ) : null}
            {result.ok ? (
              <p className="text-slate-500 dark:text-slate-400">
                Сверено полей: {fieldCount}
                {result.tabs_read.length > 0
                  ? `, разделов прочитано: ${result.tabs_read.length}`
                  : ""}
                {result.documents.length > 0
                  ? `, документов найдено: ${result.documents.length}`
                  : ""}
              </p>
            ) : null}
            {result.warnings.length > 0 ? (
              <ul className="list-disc space-y-0.5 pl-5 text-amber-700 dark:text-amber-300">
                {result.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : null}
            {result.documents.length > 0 ? (
              <ul className="space-y-0.5 pt-1">
                {result.documents.slice(0, 5).map((document) => (
                  <li key={document.document_url}>
                    <a
                      href={document.document_url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="underline"
                    >
                      {document.document_name}
                    </a>
                    <span className="text-slate-400">
                      {" "}
                      ({document.document_type})
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {analysis ? (
          <div className="space-y-3 rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-700">
            <div>
              <p className="font-medium text-slate-900 dark:text-white">
                Анализ страницы {analysis.ticker}
              </p>
              <p className="mt-1 text-slate-700 dark:text-slate-200">
                {analysis.summary}
              </p>
            </div>

            <p className="text-xs text-slate-500 dark:text-slate-400">
              Прочитано полей: {analysis.analysis.fields_extracted}; вкладок: {analysis.analysis.tabs_read.length}; действий в браузере: {analysis.browser.navigation_steps}. Итог: {analysis.generated_by === "llm" ? `локальный AI ${analysis.model ?? ""}` : "проверяемый расчетный движок"}.
            </p>

            {analysis.analysis.mismatches.length > 0 ? (
              <div className="rounded-lg bg-rose-50 px-3 py-2 text-rose-900 dark:bg-rose-950 dark:text-rose-200">
                <p className="font-medium">Расхождения с базой</p>
                <ul className="mt-1 list-disc pl-5">
                  {analysis.analysis.mismatches.map((item) => (
                    <li key={item.field}>
                      {item.field}: KASE — {item.on_page}, база — {item.in_database}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {analysis.analysis.findings.some(
              (item) => item.kind === "warning" || item.kind === "limitation",
            ) ? (
              <ul className="list-disc space-y-1 pl-5 text-amber-700 dark:text-amber-300">
                {analysis.analysis.findings
                  .filter(
                    (item) =>
                      item.kind === "warning" || item.kind === "limitation",
                  )
                  .map((item) => <li key={item.code}>{item.message}</li>)}
              </ul>
            ) : null}
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}
