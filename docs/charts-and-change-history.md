# Charts and change history without licensed KASE data

KASE sells its archived trading information as a commercial product. The
application therefore never needs it to draw a chart: price, activity and yield
history are assembled from the snapshots the collectors already take of the
**public** endpoints, and the change feed comes from the incremental ingestion
log. The licensed importer (`docs/forecast/history-import.md`) remains an
optional operator tool for model training; it is not a prerequisite for the UI.

## Where a session comes from

| Series | Stored rows used | Public source |
|---|---|---|
| Share price / activity | `stock_quotes` | `/api/instruments/securities/` |
| Bond price / yield | `bond_quotes` | `/api/trade-results/bonds/` |
| Bond session deal | `bond_trades` | `/api/trade-results/bonds/` (one aggregated record per session) |
| Change markers and feed | `data_change_sets` | whichever source the change was detected in |

`PublicSeriesService` (`backend/app/services/series_service.py`) folds those rows
into one bar per KASE trading session.

## The rules the folding follows

* **A bar states how it was made.** `bar_basis` is `native` when the source row
  carried an exchange-published OHLC, and `sampled` when the bar was folded out
  of our own polling. On a sampled bar the high and low are the extremes *we
  observed*, and `observations` says how many points that rests on. The UI
  repeats this in the tooltip and in the table.
* **Running totals are never summed.** KASE publishes `vol`, `volkzt` and
  `dealcnt` as session-to-date figures, so two snapshots of one session are two
  views of the same number. The session value is the maximum.
* **Zero is not a price.** A zero or missing price leaves the field `null`; the
  session stays in the series with no close rather than being drawn at zero.
* **Licensed rows are excluded and counted.** Rows whose `source` is
  `kase_licensed_archive` are dropped unless the caller passes
  `include_licensed=true`. `coverage.licensed_rows_excluded` reports how many
  were dropped, so "no licensed data" is a checkable claim, not a promise.
* **Coverage never exceeds 100%.** `coverage_ratio` divides the sessions that
  fall on days the trading calendar calls open by the trading days in the
  window; sessions outside the calendar are reported as
  `sessions_outside_calendar` instead of inflating the ratio.
* **Two sessions minimum.** With fewer, `coverage.chartable` is `false` and the
  UI explains that history is still accumulating rather than drawing a line
  through a single point.

## Endpoints

```
GET /api/v1/stocks/{identifier}/series?days=365[&include_licensed=true]
GET /api/v1/bonds/{identifier}/series?days=365[&include_licensed=true]

GET /api/v1/stocks/{identifier}/changes           # feed, filterable
GET /api/v1/stocks/{identifier}/change-summary     # counts + freshness
GET /api/v1/bonds/{identifier}/changes
GET /api/v1/bonds/{identifier}/change-summary
GET /api/v1/instruments/{identifier}/changes       # type-agnostic
```

A series response carries `sessions`, `markers` (material changes grouped onto
the session they were detected in), `coverage` and a plain-language `warning`
when the data needs a caveat - demo mode, sampled extremes, or too little
history.

## Frontend

* `frontend/features/charts/SeriesPanel.tsx` - the range filter row plus the
  price chart, the activity chart and (for bonds) the yield chart, a table view
  of the same numbers, and the coverage footer. One measure per plot: there is
  no second y-axis anywhere.
* `frontend/features/charts/ChangeHistoryPanel.tsx` - the change feed, grouped
  by day, filterable by section and materiality, each row linking to its source.
* Chart colours live in `.viz` tokens in `frontend/app/globals.css`. Both light
  and dark steps were validated for colour-vision separation and for contrast
  against the surface they render on; change them only by re-validating.

## Growing the history

The series is only as long as our own collection history. Each catalogue or
trade-results refresh appends a session, so coverage improves with uptime:
`scripts/refresh.py` and the scheduler in `backend/app/jobs/` are what make the
charts fill in. Nothing here backfills from a paid archive.
