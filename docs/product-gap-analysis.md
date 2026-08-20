# KASE Investment AI — product gap analysis

Audit date: 2026-08-20. Branch `main` @ `f00a4a1d`.
Baseline test run before any change: **548 passed, 22 skipped, 1 failed**
(`tests/test_stock_forecast.py::test_peer_market_feature_waits_for_exact_quote_timestamp`).

## How to read this

Each row is one numbered requirement from the product brief.

| Mark | Meaning |
| --- | --- |
| **EXISTS** | Implemented and covered by the existing architecture. |
| **PARTIAL** | Implemented, but a named part of the requirement is not reachable. |
| **MISSING** | No implementation. |
| **BROKEN** | Implemented but not working as specified. |

The repository turned out to be far more complete than the brief assumes. The
great majority of the fifty sections are **EXISTS**. Rebuilding any of them
would destroy working functionality, so this document records what is already
there as carefully as what is not — the point of the audit is to stop work from
being duplicated.

## Where the product lives

| Concern | Location |
| --- | --- |
| API | `backend/app/api/routes/` — 15 routers behind `settings.API_PREFIX` |
| Deterministic calculations | `backend/app/calculations/` |
| Scoring (strict engine) | `backend/app/scoring/strict/` |
| KASE collection | `backend/app/collectors/`, `backend/app/providers/`, `backend/app/browser/` |
| History and backfill | `backend/app/services/backfill/`, `backend/app/models/history.py` |
| Monitoring | `backend/app/services/monitoring.py`, `backend/app/jobs/scheduler.py` |
| Forecast | `backend/app/forecast/`, `backend/app/services/stock_forecast.py` |
| News | `backend/app/services/news_*.py`, `backend/app/collectors/news.py` |
| Frontend | `frontend/app/`, `frontend/features/` (Next.js) |
| Migrations | `migrations/versions/` — 14 revisions |

## Findings

| § | Requirement | Status | Evidence / note |
| --- | --- | --- | --- |
| 1 | Data / calculation / AI separated | EXISTS | `calculations/` and `scoring/` are pure; `ai/` and `backend/app/ai/` only explain. |
| 2 | Works without a KASE API key | EXISTS | `KaseDataMode` includes `public_api`, `website`, `browser`; `KASE_API_KEY` is optional. Vercel default is `public_api`. |
| 3 | Dynamic instrument discovery | EXISTS | `collectors/kase_stock_catalog.py`, `collectors/incremental_catalog.py`. `Instrument` carries `first_seen_at` / `last_seen_at` / `is_active`; `20260818_0100_stock_delisted_at` preserves delisted rows. |
| 4 | Two-year backfill, nothing fabricated | EXISTS | `services/backfill/` (11 modules). `HISTORICAL_BACKFILL_YEARS=2`, window computed relative to run time in `backfill/window.py`. |
| 5 | Permanent historical storage | EXISTS | `models/history.py`, `models/news.py`, `models/financials.py`; corrections in `20260818_0010_observation_corrections`. |
| 6 | History grows forever | EXISTS | No retention job deletes normalized history; `RAW_SNAPSHOT_RETENTION_DAYS` applies to raw snapshots only. |
| 7 | 10-minute server-side monitoring | EXISTS | `jobs/scheduler.py` runs `refresh_monitoring` on `MONITORING_INTERVAL_SECONDS=600`, started from the app lifespan — independent of the frontend. |
| 8 | Smart change detection | EXISTS | `services/fast_check.py`, `services/incremental.py`, per-section hashes in `models/incremental.py`. |
| 9 | Store changes, not duplicates | EXISTS | `last_checked_at` / `last_changed_at` plus fingerprints; `tests/test_incremental_ingestion.py`. |
| 10 | Stock scoring | EXISTS | `scoring/strict/stocks.py` with the briefed component weights. |
| 11 | Value-trap detection | EXISTS | `scoring/strict/redflags.py`. |
| 12 | Bond scoring | EXISTS | `scoring/strict/bonds.py`. |
| 13 | Real return after inflation | EXISTS | `calculations/returns.py`; `inflation_enabled` / `show_real_return` in `schemas/settings.py`, default on; `GET /settings/inflation` explains the rate used. |
| 14 | Separate bank scoring | EXISTS | `scoring/strict/banks.py`, `POST /scoring/bank`. |
| 15 | Hard caps | EXISTS | `scoring/strict/caps.py`; `tests/test_strict_scoring.py`. |
| 16 | Confidence separate from score | EXISTS | `scoring/strict/confidence.py`; `confidence` is its own column on `StrictScoreSnapshot`. |
| 17 | Every score explained | EXISTS | `scoring/strict/explain.py`, `GET /scoring/snapshot/{id}`. |
| 18 | TOP 100 | **PARTIAL** | Stock categories cover the brief (`stock_ranking.SCORE_BY_CATEGORY`) and allow `limit<=100`. **`GET /bonds/top` caps `limit` at 50, so a bond TOP 100 cannot be requested.** |
| 19 | Universal search | EXISTS | `GET /instruments/search` matches ticker, ISIN, company and issuer across both asset classes; an exact ticker/ISIN sorts first. |
| 20 | Calculator accepts any KZT amount | EXISTS | `amount: float = Field(gt=0)` — free numeric input, no preset enum. |
| 21 | Historical chart ranges | **PARTIAL** | `chart_service.RANGES` has `1d 5d 1m 3m 6m 1y 2y max`. **`3y` and `5y` are absent**, so two ranges the brief names cannot be requested. |
| 22 | Chart event markers | EXISTS | `services/historical_events.py`, `features/charts/SeriesPanel.tsx`. |
| 23 | News beside the stock | EXISTS | `GET /stocks/{id}/news`, `features/stocks/NewsImpactPanel.tsx`, Tengrinews collector. |
| 24 | News impact | EXISTS | `services/event_study.py`, `GET /stocks/{id}/event-impact`. |
| 25 | Separate forecast engine, no leakage | EXISTS | `forecast/pipeline.py` gates every feature on `available_at`. |
| 26 | Forecast chart, clearly separated | EXISTS | `features/stocks/ForecastPanel.tsx`. |
| 27 | Historical score | **PARTIAL** | Snapshots are stored and served by `GET /scoring/history/{ticker}`. **The briefed path `GET /instruments/{identifier}/score-history` does not exist**, and the existing route resolves tickers only — not ISIN. |
| 28 | Point-in-time correctness | EXISTS (one test BROKEN) | `scoring/strict/pit.py`; the forecast context gates on `available_at`. The failing baseline test was a test-isolation defect, not a product defect — see below. |
| 29 | Complete instrument page | EXISTS | `frontend/app/stock/[identifier]/page.tsx` composes calculator, chart, forecast, score breakdown, change history, news, financials and sources. |
| 30 | "What changed?" | EXISTS | `services/change_service.py`, `GET /stocks/{id}/change-summary`, `features/charts/ChangeHistoryPanel.tsx`. |
| 31 | Comparison | EXISTS | `POST /stocks/compare` (like-for-like) and `POST /instruments/compare` (cross-asset, explicitly refusing to equate YTM with scenario growth). |
| 32 | Portfolio | EXISTS | `services/portfolio_service.py`, `calculations/portfolio.py`. |
| 33 | Portfolio chart | EXISTS | `features/portfolio/PortfolioView.tsx`. |
| 34 | Favourites / watchlist | EXISTS | `/watchlist` routes; `services/ingestion_priority.py` raises monitoring priority for watched rows. |
| 35 | Settings page | EXISTS | `GET` / `PUT /settings`, `features/settings/SettingsForm.tsx`. |
| 36 | Simple mode | EXISTS | `stores/uiStore.ts`, `components/layout/ModeToggle.tsx`. |
| 37 | Pro mode | EXISTS | Same toggle; Pro blocks on the detail pages. |
| 38 | Home page | EXISTS | `features/home/HomeExplorer.tsx`. |
| 39 | User profiles affect ranking only | EXISTS | `profile` is a query parameter on listing routes; the stored score is untouched. |
| 40 | Data source labels | EXISTS | `data_mode` on every response; `components/layout/DataModeBanner.tsx`. `DataMode` has no `real_time` member. |
| 41 | Monitoring health | **PARTIAL** | `GET /health/kase-browser` exists. **`GET /health/monitoring` does not, and no monitoring cycle telemetry is persisted** — cycle results are written to the log and then discarded. |
| 42 | Backfill status | EXISTS | `GET /admin/backfill/status`, admin-token protected. |
| 43 | Error safety | EXISTS | `services/backfill/validate.py`, `IngestionAnomaly`, `GET /admin/backfill/anomalies`. |
| 44 | Idempotency | EXISTS | Content hashes and unique constraints throughout; `tests/test_backfill.py`. |
| 45 | Performance | EXISTS | Browser pool, pacing and backoff in `browser/session.py` and `browser/pacing.py`. |
| 46 | Mobile UX | EXISTS | Tailwind responsive layouts on every page. |
| 47 | README | EXISTS | `README.md`, 32 KB, documents the architecture and the non-negotiable rules. |
| 48 | Tests | EXISTS | 29 test modules, 548 passing, with golden strict-scoring fixtures. |

## The one BROKEN item, and what it was

`test_peer_market_feature_waits_for_exact_quote_timestamp` failed in a full-suite
run and passed in isolation. The cause was **test pollution, not a product bug**:
other test modules commit stocks into the shared SQLite database and never clean
up, so by the time this test ran, 39 foreign stocks sat in the cross-section.
The market factor legitimately averages every stock in the database, so one peer
doubling its close moves it by roughly `log 2 / n_peers` — the test's `> 0.5`
threshold silently assumed it was the only peer.

The point-in-time gate itself was correct. The fix pins the assertion to a
sector unique to the test, where the cross-section is fully controlled, and
keeps a direction-only assertion on the market factor.

## Work this audit authorises

Only four gaps are real, and all four are extensions of existing architecture:

1. **§41** — persist monitoring cycle telemetry and serve `GET /health/monitoring`.
2. **§27** — add `GET /instruments/{identifier}/score-history`, resolving ticker *or* ISIN, for stocks and bonds alike.
3. **§21** — add the `3y` and `5y` chart ranges.
4. **§18** — raise the `/bonds/top` limit to 100.

Nothing else in the brief requires new code.
