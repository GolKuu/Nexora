# Stock Forecast AI — model validation report

Status: **production release not yet eligible on the bundled KASE dataset**

Report date: 2026-08-17

Feature version: `stock-features-v3`

Model family: `kase-quantile-ensemble-v3`

This report deliberately contains no invented win rate. The bundled verified
snapshot contains 87 KASE stocks and 87 stock quote rows: one observation per
instrument. Therefore no instrument satisfies the minimum 120/125/150/210
daily observations for the 1d/5d/20d/60d horizons, and there is no honest
production metric table to publish yet.

## Dataset audit

| Item | Verified current state |
|---|---:|
| Markets | KASE equities |
| Instruments | 87 |
| Price observations | 87 |
| Earliest stored timestamp | varies by instrument; one row each |
| Latest bundled capture | 2026-08-17 snapshot |
| Public refresh cadence | 10 minutes |
| Forecast-eligible instruments | 0 |

Corporate actions (731 bundled rows), financial periods (656), dividends (26),
news events and quote provenance are stored separately and aligned by their
availability timestamps. Delisted/inactive instruments remain in persistence
instead of being removed from backtests.

Forward labels must land on the exact target KASE trading session. If an
illiquid instrument has no observed price for that session, the sample is
omitted instead of using a later trade and silently changing the horizon.
Lagged return features follow the same rule and include per-horizon
availability flags when the exact prior-session price is absent.

## Candidate protocol

For every eligible instrument and horizon the training job compares:

1. naive no-change;
2. historical mean return;
3. market-return proxy baseline;
4. regularised linear quantitative model.

Selection uses expanding-window folds. The final chronological 15% is an
untouched temporal test; random train/test splitting is not used. The candidate
report records MAE return, RMSE, direction and balanced accuracy, Brier score,
log loss, calibration bins/ECE, quantile loss, Spearman rank correlation,
information coefficient, and 50%/80% interval coverage. A complex candidate is
not promoted when it fails the baseline gate.

## Metrics by horizon

| Horizon | Production model | OOS observations | RMSE | Brier | 50% coverage | 80% coverage |
|---|---|---:|---:|---:|---:|---:|
| 1d | not released | 0 | — | — | — | — |
| 5d | not released | 0 | — | — | — | — |
| 20d | not released | 0 | — | — | — | — |
| 60d | not released | 0 | — | — | — | — |

## Verification evidence

The deterministic test dataset is used only to prove mechanics, never as a
product performance claim. Tests verify feature time alignment, missing-day
handling, split adjustment, baseline comparison, expanding-window folds,
quantile ordering, interval metrics, reproducible paths, registry persistence,
snapshot idempotency, stale/illiquid confidence reduction and insufficient-
history refusal. The full project suite currently passes.

## Known failure modes

- sparse KASE trading creates stale prices and weak sample coverage;
- regime shifts can make current features out-of-distribution;
- public best bid/ask is not full order-book depth;
- a price-return series does not include dividend total return;
- future exchange holiday changes require calendar updates;
- event associations are not causal claims;
- the bundled repository cannot truthfully validate a production model until
  more history is accumulated or a licensed historical archive is imported.

## Release checklist

The training job will replace the empty metric cells only after every number is
computed from stored out-of-sample forecasts. Until then the UI shows history,
the minimum-observation reason and no future band.
