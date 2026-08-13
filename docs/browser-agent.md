# Browser Agent

The browser agent reads the **public** KASE website the way a person does: a
real Chromium, real JavaScript, real clicks. It exists because a great deal of
what KASE publishes is on a page rather than behind a contracted API - and a
public page needs no API key.

> **No `KASE_API_KEY` is required** for `KASE_DATA_MODE=browser`,
> `website_structured` or `auto`. Only `official_api` needs one.

---

## 1. Where it sits

```
                    ┌──────────────────────────────────────┐
official API ─────► │                                      │
browser agent ────► │  provider chain  →  collector  →  DB │ → calculation
plain HTML reader ► │      (validated, provenance-stamped) │    engine → scoring
last verified DB ─► └──────────────────────────────────────┘
```

The browser is **an additional source layer**, not a replacement for anything.
No financial mathematics happens inside a browser script (§56); values leave
the agent as `raw` + `normalized` pairs and the engine does the rest.

## 2. Modules

| Module | Responsibility |
|---|---|
| `browser/session.py` | `BrowserService` (one engine) and `BrowserSession` (tabs, cookies, downloads, navigation log) plus every primitive action |
| `browser/locators.py` | The strategy ladder: role → text → label → attribute → structure → CSS |
| `browser/toolbox.py` | The `browser.*` command contract; the only surface an AI planner may drive |
| `browser/pacing.py` | Concurrency cap, minimum interval, exponential backoff, runtime budget |
| `browser/cache.py` | Per-URL, per-kind TTL cache of parsed results |
| `browser/extractors/text.py` | `KaseTextExtractor` - main content, chrome removed, raw kept |
| `browser/extractors/tables.py` | Static and dynamic (paginated / infinite-scroll) tables |
| `browser/extractors/tabs.py` | `KaseTabExplorer` - discovers the page's own sections |
| `browser/extractors/documents.py` | Official PDF/XLSX/DOCX links with metadata |
| `browser/extractors/fields.py` | KASE's label vocabulary → our field names |
| `browser/extractors/page.py` | `PageExtractor` - one page, everything on it |
| `browser/normalize.py` | Numbers, percentages, dates, currencies |
| `browser/validator.py` | `DataValidator` - source priority, cross-checks, sanity |
| `browser/visual.py` | `KaseVisualAnalyzer` + chart/tooltip handling |
| `browser/agent.py` | `KaseBrowserAgent` - the catalogue, instrument and search flows |
| `providers/kase_browser.py` | `BondDataProvider` adapter so the rest of the app is unaware |
| `services/browser_agent_service.py` | Orchestration, persistence, refresh de-duplication |

## 3. Verified page map (August 2026)

Confirmed by driving a real browser against the live site:

| Page | URL |
|---|---|
| Home | `https://kase.kz/ru/` |
| Bond catalogue | `https://kase.kz/ru/markets/corporate-bonds` (`/ru/bonds/` redirects here) |
| Instrument | `https://kase.kz/ru/investors/bonds/{TICKER}` |
| Issuer | `https://kase.kz/ru/listing/issuers/{CODE}` |
| Instrument list | `https://kase.kz/ru/investors/instruments` |

The site is an Angular SSR application: the instrument data is present in the
server-rendered DOM, so extraction is DOM-based and no private endpoint is
involved. `PATHS` in `browser/agent.py` is the only place these strings live.

### Instrument page sections

Discovered dynamically. On a typical bond page they are `Торги`,
`Характеристики ценной бумаги`, and the chart selectors `чистая цена`,
`грязная цена`, `доходность`. The tab names are **never** hardcoded as the
means of navigation - `SECTION_VOCABULARY` only *ranks* what discovery found.

## 4. The rules that constrain it

| # | Rule | Where it is enforced |
|---|---|---|
| §2 | Public data needs no API key | `providers/factory.py`, `Settings.validate_runtime` |
| §6 | No single selector is load-bearing | `locators.py` |
| §10 | Every loop is bounded | `BROWSER_MAX_PAGES/SCROLLS/ROWS/RUNTIME_S` |
| §13/§14 | A chart image is not a price | `validator.NEVER_FROM_VISUAL` |
| §21 | CAPTCHA is reported, never solved | `session.detect_blocks` |
| §22 | Login walls are reported, never bypassed | `session.detect_blocks` |
| §23 | Politeness: pacing, backoff, session reuse | `pacing.py`, provider session reuse |
| §24 | Recently-read pages are not re-read | `cache.py` |
| §29 | Source priority | `validator.METHOD_PRIORITY` |
| §30 | Conflicts warn, never coin-flip | `validator._conflict` |
| §38 | AI plans, it does not write to the DB | `toolbox.run_plan` → `DataValidator` → repository |
| §44 | The raw string is never lost | `ExtractedValue.raw` |
| §49 | The user sees a source and a time | `browser_agent_service._report` |
| §54 | Only kase.kz, only as a normal visitor | `agent.confirms_domain` |

## 5. Extraction methods and confidence

Every value states how it was read:

| method | default confidence | may supply prices? |
|---|---|---|
| `dom` | 0.99 | yes |
| `table` | 0.97 | yes |
| `tooltip` | 0.90 | yes |
| `document` | 0.85 | yes |
| `visual` | 0.35 | **no** |

`visual` is capped at 0.6 and is refused outright for price, coupon, ISIN and
date fields. Seeing a line rise from "about 95 to about 101" is a *trend*, not
two quotations.

## 6. Setup

```bash
pip install -r backend/requirements.txt
python -m playwright install chromium     # once: downloads the engine
```

```env
KASE_DATA_MODE=browser        # or auto
BROWSER_ENABLED=true
BROWSER_HEADLESS=true
KASE_LANGUAGE=ru
```

Everything else has a working default; see `.env.example` for the full list.

## 7. Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/bonds/{id}/verify-on-kase` | "Проверить на KASE" - controlled refresh of one instrument |
| `GET` | `/api/v1/bonds/{id}/kase-tab/{section}` | Read one section of the instrument page |
| `GET` | `/api/v1/bonds/{id}/kase-link` | "Открыть на KASE" - the official confirmed URL |
| `POST` | `/api/v1/browser/catalog-refresh` | Sweep the public catalogue into the database |
| `POST` | `/api/v1/browser/inspect` | Read an arbitrary public kase.kz page |
| `GET` | `/api/v1/browser/status` | Engine, cache and limit diagnostics |

Concurrent calls for the same instrument are joined into one browser visit, so
an impatient user clicking ten times causes one page load.

## 8. Command contract

```
browser.open          browser.click         browser.extract_text
browser.back          browser.click_text    browser.extract_table
browser.forward       browser.click_at      browser.get_links
browser.reload        browser.hover         browser.screenshot
browser.wait          browser.hover_at      browser.get_html
browser.wait_for_selector    browser.fill   browser.get_title
browser.wait_for_content     browser.press  browser.get_url
browser.scroll        browser.tabs          browser.get_tooltip
browser.scroll_to_element    browser.tabs.open/switch/close
browser.find          browser.download      browser.documents
browser.find_text     browser.language      browser.detect_blocks
browser.snapshot
```

Each returns an `ActionResult` (`status`, `value`, `error`, `duration_ms`,
`url`) and appends a line to the session's navigation log.

## 9. Provenance

Two tables:

* **`raw_browser_snapshots`** - URL, title, `html_hash`, visible text, the
  structured extraction, screenshot path, browser and extractor versions,
  session id, language, status.
* **`browser_navigation_log`** - session id, action number, action, target,
  URL before and after, status, duration, error. No credentials, ever.

## 10. Limitations found on the live site

* kase.kz rejects the default headless user-agent with an HTTP 500 "blocked"
  page. `BROWSER_USER_AGENT` therefore defaults to an ordinary desktop Chrome
  string. This is identification, not evasion - the agent still respects rate
  limits, CAPTCHAs and logins.
* Ratings are published as free-text news, not structured data, so
  `get_ratings()` returns nothing rather than parsing a headline.
* The instrument page states the *number* of outstanding securities, not an
  outstanding *amount*. The two are not the same and one is not silently
  derived from the other.
* Financial figures on the issuer page are KASE's own aggregates. Full IFRS
  statements live in the linked PDFs and belong to the document pipeline.
* Charts are SVG with no exposed series and no tooltip on hover for the pages
  checked, so chart values come from the trades table, not from the picture.

## 11. Tests

```bash
pytest tests/test_browser_offline.py          # no network; skips if no engine
RUN_LIVE_BROWSER_TESTS=true pytest -m live_browser -s
```

The live test performs the full §55 scenario against kase.kz and prints what it
actually found at each step.
