# KASE one-year chart capture

This job inventories the official public KASE stock and bond catalogs, stores
the browser-visible structured daily history in the application's existing
permanent history tables, and records an auditable result for every discovered
instrument. It never derives numerical history from screenshots.

## Commands

Run from the repository root with the backend on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.jobs.capture_kase_1y_charts --all --resume
```

Useful narrower modes:

```powershell
python -m app.jobs.capture_kase_1y_charts --stocks --data-only --only-missing
python -m app.jobs.capture_kase_1y_charts --bonds --screenshots-only --resume
python -m app.jobs.capture_kase_1y_charts --bonds --ticker JSBNb4 --resume
```

The default output is `data/kase_1y_capture/`:

- `instruments.json` and `instruments.csv` are the resumable manifest.
- `report.json` and `docs/kase-1y-capture-report.md` contain aggregate and
  per-instrument outcomes.
- `screenshots/{stocks,bonds}/` contains only verified 1Y chart regions.
- `metadata/` contains screenshot provenance and range metadata.

## Data and identity rules

- The public KASE catalogs define `ALL` at the capture timestamp; lists are not
  hard-coded.
- A canonical `Instrument(instrument_type="bond")` mirrors each existing
  `Bond` identity so both asset classes can reuse `MarketObservation`,
  `DailyMarketSnapshot`, validation, coverage, and checkpoint services. The
  existing bond tables and API identities remain intact.
- The KASE chart response is validated before storage. Missing history is
  `UNAVAILABLE`; short listing history is `PARTIAL`; rejected input is never
  converted into synthetic prices.
- Inactive catalog instruments are explicitly recorded as
  `INSTRUMENT_DELISTED` rather than queried as if they were current listings.
- Screenshots require ticker or ISIN identity on the opened KASE page, a visible
  chart region, and an explicit 1Y selector. A page without those elements is
  recorded as `NO_CHART` or `NO_1Y_SELECTOR`; no substitute image is created.

## Product integration

The stock and bond detail APIs already serve `PublicSeriesService`. Stock
series use canonical daily snapshots directly. Bond series now resolve the
canonical bond `Instrument` snapshot history first and merge same-day public
`BondQuote` fields such as YTM and book values when available. Existing quote
and trade folding remains the fallback.

The process is idempotent: normalized observations and daily snapshots use
existing uniqueness constraints and upserts, while one coverage record and one
checkpoint are maintained per instrument and job type.
