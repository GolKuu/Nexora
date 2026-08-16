# KASE browser data sources

This register is the production allow-list and licensing boundary for public
KASE collection. Detailed field mappings and observed schemas live in
[`../technical/kase-sources.md`](../technical/kase-sources.md).

| Class | URL | What it contains | Public/auth | Browser accessible | Structured | Frequency | Licensing note | Status |
|---|---|---|---|---|---|---|---|---|
| PUBLIC WEB | `https://kase.kz/ru` | Public KASE home page and navigation | Anonymous; no login | Yes | DOM | On demand for health only | Ordinary public page; no restriction is bypassed | Active |
| PUBLIC WEB | `https://kase.kz/ru/markets/corporate-bonds` | Discoverable bond catalogue, tickers, issuers, currencies and official links | Anonymous | Yes | DOM and HTML table | Catalogue schedule; never per user page view | Public presentation; paced and cached | Active |
| PUBLIC WEB | `https://kase.kz/ru/investors/bonds/{ticker}` | Bond identity, terms, quotes, trades, tabs and document links | Anonymous | Yes | DOM, tables, tooltips | Incremental/manual verification | Only fields visibly served to an ordinary visitor | Active |
| PUBLIC WEB | `https://kase.kz/api/instruments/securities/` | Securities catalogue used by the KASE web application | Anonymous | Observed from the public page | JSON | Catalogue schedule | Public structured source discovered from KASE's own browser traffic | Verified |
| PUBLIC WEB | `https://kase.kz/api/instruments/bonds/{ticker}/` | Bond characteristics and identity | Anonymous | Observed from the public page | JSON | Weekly or on material refresh | Endpoint is used by the public site; schema is validated before persistence | Verified |
| PUBLIC WEB | `https://kase.kz/api/trade-results/bonds/` | End-of-session bond quote/trade aggregates | Anonymous | Observed from the public page | JSON | Several times per day | Delayed/session aggregate; never labelled real-time | Verified |
| PUBLIC WEB | `https://kase.kz/api/instruments/coupon-payments/{ticker}/` | Official coupon periods and rates | Anonymous | Observed from the public page | JSON | Weekly | Public schedule; missing values remain null | Verified |
| PUBLIC WEB | `https://kase.kz/api/companies/documents/` | Public issuer documents and metadata | Anonymous | Observed from the public page | JSON plus files | Weekly / after publication | Download only linked public documents; retain versions by content hash | Verified |
| OFFICIAL API / LICENSED | Configured `KASE_API_URL` | Contract market data, if supplied by KASE | API key and contract required | No | Contract-specific | Per contract | Must not be used without an explicit KASE agreement | Disabled without key |
| UNKNOWN / REQUIRES REVIEW | Any newly observed endpoint | Unclassified response | Unknown until reviewed | Observation only | Unknown | None | Record URL/status/schema only; do not enable production ingestion | Quarantined |

## Hard boundaries

- Never bypass login, CAPTCHA, paywall, anti-bot controls, rate limits or
  licensing restrictions.
- Never capture authentication headers, foreign cookies or tokens from network
  observations.
- A newly observed structured response is diagnostic-only until its public
  anonymous behavior, source page, schema and licensing uncertainty are
  documented here.
- Real-time quotes, order-book depth and any contract-only dataset require a
  KASE agreement; cached public session data is labelled `end_of_day`,
  `delayed` or `cached`, never `live` without evidence.
