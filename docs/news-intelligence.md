# News Intelligence & Event Impact Engine

The module keeps language interpretation separate from observed market facts:

`NewsSourceProvider -> NewsArticle -> EventCluster -> MarketEvent -> EventMarketReaction`

`TengrinewsCollector` reads public RSS metadata, a short bounded extract, and a URL. It does not persist full article bodies and is never a market-price provider. Official KASE, issuer, regulator, central-bank, and statistics collectors implement the same `NewsSourceProvider` contract and receive Tier 1 source confidence.

## Incremental collection

Articles are idempotent by `source + canonical_url`; when a stable URL is unavailable providers can use normalized title plus publication time. Tracking parameters are removed before hashing. Cross-source title similarity within 72 hours creates one `EventCluster`; official and earlier sources win canonical-source selection.

Run operational tasks with:

```powershell
python scripts/news.py collect
python scripts/news.py stats
python scripts/news.py export
```

## Market facts

Reaction fields are calculated only from `StockQuote` rows. `price_before` is the last available quote at or before publication. Intraday horizons use the first quote at or after the target time. If the market is closed, alignment starts at the next observed trading session. Trading-day horizons use observed sessions rather than calendar days.

Abnormal return v1 is `stock return - benchmark return`. The engine prefers an explicit broad benchmark instrument (`KASE`, `KASE_INDEX`, or `SPY`); if none exists it leaves benchmark and abnormal returns null. Volume ratio uses the preceding rolling observations, never a global constant.

Sentiment describes the article's language. It is not a forecast and is not copied into factual reaction fields. Surprise stays null unless both actual and consensus are available.

## Historical analogs and training data

Analogs rank event type, issuer, sector, market regime, and importance. Rates and medians are hidden below the configured minimum sample (default 5). The export writes `datasets/events/event_training_dataset.jsonl` with separate `features` and `labels` objects. Validation rejects future-return fields in features; future returns exist only in labels.

## API

- `GET /stocks/{identifier}/news`
- `GET /stocks/{identifier}/events`
- `GET /stocks/{identifier}/event-impact`
- `GET /stocks/{identifier}/daily-drivers`
- `GET /events/{id}`
- `GET /events/{id}/historical-analogs`

Daily drivers intentionally say that movement “coincided with” events. They never claim causality from temporal correlation alone.
