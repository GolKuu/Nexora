"""Reading history from kase.kz as an ordinary public visitor.

The browser opens the official instrument page, walks its own tabs, follows
pagination and "show more" controls, and reads the rendered tables. Preference
order is structured public data, then DOM tables, then official documents -
visual analysis is a last resort and never a source of numbers.

Explicit non-goals, enforced here rather than left to good intentions:

* no authentication, no CAPTCHA solving, no paywall or licence circumvention -
  when the site says no, the crawl records the condition and stops;
* no private endpoints; the network observer only *lists* the public requests
  the page makes itself, as metadata, to find more structured public data;
* no aggressive crawling - one browser context by default, a delay between
  requests, exponential backoff on failure.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from app.browser.agent import KaseBrowserAgent, KaseBrowsingContext
from app.browser.extractors.documents import extract_documents, extract_publication_links
from app.browser.extractors.tables import extract_dynamic_table, extract_tables
from app.browser.network import KaseNetworkObserver
from app.browser.types import BrowserStatus
from app.core.config import settings
from app.services.backfill.chart_api import KaseChartHistoryClient
from app.core.logging import get_logger
from app.services.backfill.parser import (
    looks_like_dividend_table,
    looks_like_price_table,
    looks_like_trade_table,
    parse_dividends,
    parse_price_history,
    parse_publication_links,
    parse_report_documents,
    parse_trades,
)
from app.services.backfill.records import CollectionResult
from app.services.backfill.window import BackfillWindow

logger = get_logger(__name__)

SOURCE_NAME = "kase_public_website"

#: Tabs worth opening on an equity page, best first. Names are only used for
#: *ranking* the tabs the page itself exposes - nothing is assumed to exist.
HISTORY_SECTIONS = ("trades", "history", "payments", "financials", "documents", "news")


@dataclass(slots=True)
class PageHistory:
    """What one instrument page yielded, before validation."""

    result: CollectionResult
    status: str
    observed_endpoints: list[dict]
    error: str | None = None


class KaseHistoryCollector:
    """Browser-driven historical collection for one instrument at a time."""

    def __init__(
        self,
        *,
        delay_ms: int | None = None,
        page_limit: int | None = None,
        chart_client: KaseChartHistoryClient | None = None,
    ):
        self.delay_ms = settings.BACKFILL_REQUEST_DELAY_MS if delay_ms is None else delay_ms
        self.page_limit = settings.BACKFILL_PAGE_LIMIT if page_limit is None else page_limit
        self.chart_client = chart_client or KaseChartHistoryClient()

    async def _pace(self) -> None:
        """Deliberate politeness: kase.kz is a public site, not a target."""
        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000.0)

    async def collect(
        self,
        ticker: str,
        window: BackfillWindow,
        *,
        url: str | None = None,
        since: datetime | None = None,
        agent: KaseBrowserAgent | None = None,
    ) -> PageHistory:
        """Collect everything the public page exposes for ``ticker``.

        ``since`` is the resume point from the checkpoint: rows older than it
        are still parsed (they are cheap once the table is on screen) but the
        crawl stops paginating further back once it passes the window start.
        """
        if agent is not None:
            return await self._collect_with(agent, ticker, window, url=url, since=since)
        async with KaseBrowsingContext(label=f"backfill-{ticker}") as owned:
            return await self._collect_with(owned, ticker, window, url=url, since=since)

    async def _collect_with(
        self,
        agent: KaseBrowserAgent,
        ticker: str,
        window: BackfillWindow,
        *,
        url: str | None,
        since: datetime | None,
    ) -> PageHistory:
        result = CollectionResult()
        target = url or agent.url_for("bond", ticker=ticker)
        if not agent.confirms_domain(target):
            return PageHistory(
                result=result,
                status=BrowserStatus.ERROR.value,
                observed_endpoints=[],
                error=f"refusing to browse a non-KASE host: {target}",
            )

        page = await agent.open_bond(
            ticker,
            url=target,
            sections=list(HISTORY_SECTIONS),
            max_tabs=len(HISTORY_SECTIONS),
            with_screenshot=False,
            with_visual=False,
            with_chart=False,
            use_cache=False,
        )
        observer = KaseNetworkObserver(agent.session)
        endpoints = observer.observed_endpoints()

        if page.browser_blocked_by_captcha or page.requires_authentication:
            # The site asked for something we will not do. Record and stop.
            result.blocked = True
            result.notes.append(
                "kase.kz потребовал аутентификацию или проверку CAPTCHA — сбор остановлен."
            )
            return PageHistory(
                result=result,
                status=page.status,
                observed_endpoints=endpoints,
                error=page.error,
            )
        if page.status != BrowserStatus.OK.value:
            return PageHistory(
                result=result, status=page.status, observed_endpoints=endpoints, error=page.error
            )

        result.source_urls.append(page.url or target)
        result.pages_visited += 1

        # Structured public data first: the page's own chart feed carries the
        # full daily series, so the rendered tables only have to cover what it
        # does not (trades, dividends, documents).
        await self._harvest_chart_history(result, ticker, window)

        # The landing tab first, then whatever sections the page itself offers.
        await self._harvest_current_page(agent, result, page.url or target, window)
        for tab in getattr(page, "tabs", []) or []:
            name = (getattr(tab, "name", None) or "").strip()
            if not name:
                continue
            await self._pace()
            await self._harvest_current_page(
                agent, result, page.url or target, window, section=name
            )
            if result.pages_visited >= self.page_limit:
                result.notes.append("достигнут лимит страниц за один проход")
                break

        return PageHistory(
            result=result, status=BrowserStatus.OK.value, observed_endpoints=endpoints
        )

    async def _harvest_chart_history(
        self, result: CollectionResult, ticker: str, window: BackfillWindow
    ) -> None:
        """Read the daily series from the public chart feed the page itself uses.

        Failure here is not fatal: the DOM tables below remain the fallback, and
        an instrument the feed does not cover simply yields no bars.
        """
        await self._pace()
        try:
            bars = await self.chart_client.daily_history(ticker, window)
        except Exception as exc:  # a feed outage must not kill the crawl
            logger.info("chart history failed for %s: %s", ticker, exc)
            result.notes.append(f"публичный график недоступен для {ticker}: {exc}")
            return
        if not bars:
            result.notes.append(f"публичный график не вернул истории для {ticker}")
            return
        result.observations.extend(bars)
        source_url = bars[0].source_url
        if source_url and source_url not in result.source_urls:
            result.source_urls.append(source_url)

    async def _harvest_current_page(
        self,
        agent: KaseBrowserAgent,
        result: CollectionResult,
        source_url: str,
        window: BackfillWindow,
        *,
        section: str | None = None,
    ) -> None:
        """Read everything the rendered section exposes: tables, then links."""
        await self._harvest_tables(agent, result, source_url, section=section)
        await self._harvest_links(agent, result, window, section=section)

    async def _harvest_tables(
        self,
        agent: KaseBrowserAgent,
        result: CollectionResult,
        source_url: str,
        *,
        section: str | None = None,
    ) -> None:
        """Read every table currently rendered, classify it, parse it."""
        try:
            tables = await extract_tables(agent.session, section=section)
        except Exception as exc:  # a broken tab must not kill the whole crawl
            logger.info("table extraction failed for %s: %s", section, exc)
            result.notes.append(f"не удалось прочитать таблицы раздела {section}: {exc}")
            return

        for index, table in enumerate(tables):
            headers = list(table.headers or [])
            rows = [list(row) for row in table.rows or []]
            if not headers or not rows:
                continue

            # A truncated table is worth walking to its end; a short one is not.
            if table.truncated:
                await self._pace()
                try:
                    full = await extract_dynamic_table(
                        agent.session, section=section, table_index=index,
                        max_pages=self.page_limit,
                    )
                    headers = list(full.headers or headers)
                    rows = [list(row) for row in full.rows or rows]
                    result.pages_visited += 1
                except Exception as exc:
                    logger.info("pagination failed for %s: %s", section, exc)
                    result.notes.append(f"пагинация раздела {section} прервана: {exc}")

            if looks_like_trade_table(headers):
                result.trades.extend(
                    parse_trades(headers, rows, source=SOURCE_NAME, source_url=source_url)
                )
            elif looks_like_price_table(headers):
                result.observations.extend(
                    parse_price_history(headers, rows, source=SOURCE_NAME, source_url=source_url)
                )
            elif looks_like_dividend_table(headers):
                result.dividends.extend(
                    parse_dividends(headers, rows, source=SOURCE_NAME, source_url=source_url)
                )

    async def _harvest_links(
        self,
        agent: KaseBrowserAgent,
        result: CollectionResult,
        window: BackfillWindow,
        *,
        section: str | None = None,
    ) -> None:
        """Collect official documents and issuer publications from the section.

        Documents that look like periodic financial reporting become versioned
        report releases; KASE news and disclosure links become news records.
        Both are filtered to the requested window, and anything the page did not
        date is kept undated rather than guessed at.
        """
        try:
            documents = await extract_documents(agent.session, section=section)
        except Exception as exc:
            logger.info("document extraction failed for %s: %s", section, exc)
            result.notes.append(f"не удалось прочитать документы раздела {section}: {exc}")
            documents = []
        if documents:
            result.reports.extend(
                parse_report_documents(
                    documents, source=SOURCE_NAME, window_start=window.start_date
                )
            )

        try:
            links = await extract_publication_links(agent.session, section=section)
        except Exception as exc:
            logger.info("publication extraction failed for %s: %s", section, exc)
            result.notes.append(f"не удалось прочитать публикации раздела {section}: {exc}")
            return
        result.news.extend(
            parse_publication_links(links, source=SOURCE_NAME, window_start=window.start_date)
        )


__all__ = ["HISTORY_SECTIONS", "KaseHistoryCollector", "PageHistory", "SOURCE_NAME"]
