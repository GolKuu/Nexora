# Инкрементальный сбор KASE

Система читает только общедоступные страницы и публичные feeds KASE. Она не
обходит авторизацию, CAPTCHA или rate limits и не требует API key в режимах
`public_api`, `browser` и `website_structured`.

## Поток данных

Первое наблюдение создаёт нормализованный baseline по секциям
`bond_profile`, `issue_terms`, `quote`, `order_book`, `trades`, `cashflows`,
`issuer`, `ratings`, `news`, `documents` и `financials`. Для каждой секции
создаются current-state, версия состояния и field-level `DataChangeSet`.

Повторная проверка сначала использует ETag/Last-Modified, затем очищенный от
script/style/session/CSRF/динамического времени HTML hash. Совпавший hash
создаёт только `SourceCheckLog(status="unchanged")` и обновляет
`last_checked_at`. Raw HTML, history, scoring и AI при этом не запускаются.
Недостоверный fast-check безопасно переходит к deep extraction.

При реальном изменении транзакция обновляет `DataCurrentState`, добавляет
`DataStateVersion` и только изменившиеся поля в `DataChangeSet`, после чего
`RecalculationPlanner` ставит зависимые расчёты и AI-задачи. Порог
materiality влияет на AI и alerts, но не удаляет реальные изменения из
истории. Массовое исчезновение полей и нереалистичные скачки помечаются как
anomaly и не перезаписывают current-state.

Котировки пишутся в `BondQuoteCurrent` и в историю только при изменении
значимых полей. Сделки используют стабильный fingerprint и append-only
ingestion. Документы имеют `KaseDocument` + `DocumentVersion`: совпавший hash
пропускается, новый файл по старому URL создаёт следующую версию. Новости
дедуплицируются по стабильному id либо fingerprint заголовка, даты, эмитента
и URL.

## Управление

- `POST /api/v1/browser/bonds/{identifier}/refresh?force=true` — глубокая
  проверка без hash-shortcut.
- `GET /api/v1/bonds/{identifier}/changes` — change feed с `since`, `section`,
  `importance`, `limit`.
- `GET /api/v1/bonds/{identifier}/change-summary` — компактная сводка.
- `GET /api/v1/portfolios/{id}/changes` — изменения только бумаг портфеля.

Интервалы и materiality thresholds находятся в `.env`; DB остаётся source of
truth. Смена parser version запускает controlled deep reparse, смена формулы
или AI model не требует повторно читать KASE. Для новой формулы используются
сохранённые normalized states; исторический AI re-analysis выполняется только
отдельной управляемой job.

## Запуск и проверка

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m pytest tests/test_incremental_ingestion.py -q
```

`IngestionJob.metrics_json` хранит `pages_checked`, `pages_changed`,
`skipped_unchanged`, `deep_extractions`, `db_updates`, `ai_analyses`,
`ai_calls_saved`, `documents_skipped`, `anomalies` и latency из check logs.
