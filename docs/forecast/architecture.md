# Stock Forecast AI architecture

## Boundary

Price and return predictions are produced only by `backend/app/forecast`.
The language model may explain news, but it never generates a return, a
probability or a price path.

```text
KASE quotes + reports + corporate actions + MarketEvent
                    |
      point-in-time FeaturePipeline v3
                    |
    expanding-window walk-forward evaluation
                    |
  naive / historical / market / ridge candidates
                    |
          out-of-sample release gate
                    |
 ForecastModelVersion (immutable serialized model)
                    |
  10-minute feature refresh -> inference only
                    |
 ForecastSnapshot -> ForecastEvaluation -> track record
```

## Targets and distributions

The model predicts log returns at 1, 5, 20 and 60 trading days. A regularised
linear candidate is combined with nearest historical regimes. Quantiles come
from observed targets and fitted residuals; they are not widths painted around
one point estimate. The returned contract includes expected/median return,
`probability_up`, `probability_down`, q05/q10/q25/q50/q75/q90/q95, expected
volatility and confidence.

The deterministic Monte Carlo generator uses a fixed seed and empirical model
endpoints. Brownian-bridge corrections preserve intrahorizon uncertainty while
calibrating each path to a sampled endpoint. The UI receives q10/q25/median/
q75/q90 paths and labels them as model ranges.

Feature groups include adjusted OHLCV, momentum/drawdown, volatility,
liquidity and price staleness, fundamentals, cross-sectional KASE market and
sector returns, inflation, the KZT risk-free curve, USD/KZT movement, and
published event features. Named 1d/5d/20d/60d returns use exact prior KASE
sessions; missing prices produce an explicit availability flag rather than a
fabricated return or a return spanning an unknown number of sessions.

## Time correctness

- quotes are aggregated into actual KASE trading dates; intraday refreshes are
  not mistaken for separate trading days;
- forward-return labels and completed-snapshot evaluations require an observed
  price on the exact target KASE session; a later illiquid trade never stretches
  a 5d/20d horizon;
- missing days remain missing;
- features have an `available_at` timestamp and are rejected if it is after
  the training row;
- events enter a training row only when both their event and publication times
  are `<= as_of`;
- reports/metrics enter only when their stored availability timestamp is not
  later than the row;
- splits and reverse splits back-adjust prior prices and volumes; dividends
  remain price-return events because this release does not claim to be a total-return
  series;
- snapshots are immutable and realized results are evaluated only when the
  exact requested target session has an observed price.

## Training versus inference

The 10-minute market job collects a new observation, recalculates features and
creates a snapshot. It does not retrain an existing production model. Training
runs with historical point-in-time context, while inference uses the current
information timestamp for newly published news/macro inputs and preserves the
last real trade as `source_timestamp`. A post-close shock can therefore update
the model without pretending that a fresh trade occurred. Training
runs independently every 30 days. A candidate uses expanding-window folds for
selection and a separate untouched final temporal test. It is promoted only
when aggregate out-of-sample RMSE improves by at least 1%; a
rejected candidate remains recorded. If no production model exists, the market
job may perform the initial evaluated release once sufficient history exists.

## Confidence

Confidence is separate from `P(up)`. It combines data completeness, liquidity,
sample coverage, distance from the training distribution, model disagreement,
forecast dispersion and staleness. A stale KASE last trade, wide/missing spread,
few trades, short history or out-of-distribution regime lowers confidence and
adds a user-facing warning.

## Data-source limitation

The public KASE site provides current/end-of-day stock results and an intraday
chart, but KASE describes archived trading information as a paid information
product. The application does not bypass that boundary. It starts accumulating
verified public observations every ten minutes and returns
`forecast_available=false` until the minimum history and production gate are
satisfied. Importing a licensed archive is a separate, permissioned deployment
step.
