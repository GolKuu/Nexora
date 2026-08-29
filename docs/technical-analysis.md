# Factual KASE technical analysis

The stock technical-analysis module is deterministic and separate from the
fundamental Investment Score, DCF, and credit methodology. Its only numerical
inputs are stored, validated public KASE trading sessions. Missing sessions are
not interpolated and licensed archive rows are excluded.

## Product surfaces

- `GET /api/v1/stocks/{identifier}/technical-analysis` returns the cached
  Simple/Pro snapshot, indicator statuses, source coverage, and explanation.
- `GET /api/v1/stocks/{identifier}/technical-series` calculates only the
  requested overlay/panel series for `1m`, `3m`, `6m`, `1y`, `2y`, or `max`.
- Stock Detail exposes Simple and Pro views, independently scaled RSI, MACD,
  volume, OBV, and ATR panels, plus toggleable price overlays and markers.
- Compare and Watchlist use compact summaries. The ordinary stock list does
  not calculate full indicators during rendering.
- Goal Planner reads an existing technical cache only. Technical risk may add
  a staged educational execution scenario, but never changes fundamental
  selection, expected return, or Investment Score.

## All-stock precompute

Run from the repository root:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.jobs.precompute_technical_analysis
```

The job dynamically enumerates every active `Stock` in the local KASE
inventory. A stock is eligible for bulk calculation after 14 factual trading
sessions. Shorter histories are reported as `INSUFFICIENT_HISTORY`; they are
not failures and do not receive synthetic prices.

The reproducible coverage artifact is
`data/technical_analysis/coverage.json`. On 2026-08-29 it recorded:

- 86 active stocks;
- 26 eligible and cached;
- 60 with insufficient factual history;
- 25 with enough observations for SMA50;
- 24 with enough observations for SMA200;
- 0 calculation failures.

Every row records factual observation counts, date bounds, OHLC/volume
availability, public-series basis, excluded licensed-row count, cache status,
and technical confidence.

## Missing-data behavior

A stock without factual trades returns HTTP 200 with an empty technical series,
`as_of: null`, and `price_status: INSUFFICIENT_HISTORY`. SMA/EMA/RSI/MACD,
volume, OBV, and ATR expose their own readiness statuses. The client renders an
explicit unavailable state instead of treating `0 of 0` as a finished chart.
