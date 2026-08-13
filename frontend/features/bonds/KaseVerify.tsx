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
import type { KaseVerifyResponse } from "@/types/api";

interface Props {
  ticker: string;
  kaseUrl?: string | null;
}

export function KaseVerify({ ticker, kaseUrl }: Props) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<KaseVerifyResponse | null>(null);
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

  const fieldCount = result ? Object.keys(result.fields ?? {}).length : 0;

  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => void verify()} disabled={busy}>
            {busy ? "Проверяем…" : "Проверить на KASE"}
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
      </CardBody>
    </Card>
  );
}
