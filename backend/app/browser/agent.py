"""KaseBrowserAgent - the flows a person would perform on kase.kz.

Three flows, matching §25-§27:

``discover_catalog``  open the public bond market page, read the instrument
                      table (paging through it if needed), return tickers with
                      their ISIN, issuer, currency and official URL. Nothing is
                      hardcoded - the list is whatever KASE publishes.
``open_bond``         open one instrument's official page, confirm it really is
                      that instrument, read the visible text, discover the
                      page's own tabs, walk the relevant ones, collect tables
                      and document links, screenshot only when it helps.
``search``            use the site's public search when a ticker or ISIN is not
                      already known.

No KASE_API_KEY is involved anywhere in this module: everything here is what an
anonymous visitor sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

from app.browser.cache import page_cache
from app.browser.extractors.fields import catalog_field_for, values_from_labels
from app.browser.extractors.page import PageContent, PageExtractor
from app.browser.extractors.tables import extract_dynamic_table
from app.browser.extractors.tabs import KaseTabExplorer
from app.browser.locators import LocatorTarget
from app.browser.normalize import (
    find_isins,
    normalize_isin,
    normalize_ticker,
    parse_number,
)
from app.browser.pacing import RuntimeBudget
from app.browser.session import BrowserService, BrowserSession, browser_service
from app.browser.types import (
    METHOD_CONFIDENCE,
    BrowserStatus,
    DocumentLink,
    ExtractedValue,
    ExtractionMethod,
    PageSnapshot,
    TabResult,
)
from app.browser.validator import DataValidator, ValidationResult, make_value
from app.browser.visual import (
    KaseVisualAnalyzer,
    find_chart_data_in_dom,
    read_chart_tooltips,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Paths on the public site. Verified against kase.kz in August 2026; when KASE
#: reorganises, these are the only strings that need revisiting - discovery and
#: extraction do not depend on them.
PATHS = {
    "home": "/{lang}/",
    "catalog": "/{lang}/markets/corporate-bonds",
    "catalog_alt": "/{lang}/bonds/",
    "instruments": "/{lang}/investors/instruments",
    "bond": "/{lang}/investors/bonds/{ticker}",
    "issuer": "/{lang}/listing/issuers/{code}",
}

#: Which tabs of an instrument page are worth opening, best first (§26).
BOND_SECTIONS = [
    "characteristics",
    "trades",
    "documents",
    "payments",
    "financials",
    "news",
    "history",
    "related",
]


@dataclass(slots=True)
class CatalogEntry:
    ticker: str
    url: str
    isin: str | None = None
    issuer_name: str | None = None
    currency: str | None = None
    raw: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "isin": self.isin,
            "issuer_name": self.issuer_name,
            "currency": self.currency,
            "kase_url": self.url,
            "raw": self.raw,
        }


@dataclass(slots=True)
class CatalogResult:
    entries: list[CatalogEntry] = field(default_factory=list)
    snapshot: PageSnapshot | None = None
    truncated: bool = False
    status: str = BrowserStatus.OK.value
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "error": self.error,
            "truncated": self.truncated,
            "count": len(self.entries),
            "entries": [e.as_dict() for e in self.entries],
            "snapshot": self.snapshot.as_dict() if self.snapshot else None,
        }


@dataclass(slots=True)
class BondPageResult:
    ticker: str
    url: str | None = None
    status: str = BrowserStatus.OK.value
    error: str | None = None
    identity_confirmed: bool = False
    snapshot: PageSnapshot | None = None
    content: PageContent | None = None
    tabs_available: list[dict] = field(default_factory=list)
    #: Price-view toggles read inside a tab (clean / dirty / yield).
    views_read: list[TabResult] = field(default_factory=list)
    tabs_read: list[TabResult] = field(default_factory=list)
    documents: list[DocumentLink] = field(default_factory=list)
    candidates: list[ExtractedValue] = field(default_factory=list)
    validation: ValidationResult | None = None
    chart: dict = field(default_factory=dict)
    visual: dict | None = None
    screenshots: list[str] = field(default_factory=list)
    navigation_log: list[dict] = field(default_factory=list)
    unmapped_labels: dict[str, str] = field(default_factory=dict)
    browser_blocked_by_captcha: bool = False
    requires_authentication: bool = False

    def as_dict(self, *, include_text: bool = True) -> dict:
        return {
            "ticker": self.ticker,
            "url": self.url,
            "status": self.status,
            "error": self.error,
            "identity_confirmed": self.identity_confirmed,
            "browser_blocked_by_captcha": self.browser_blocked_by_captcha,
            "requires_authentication": self.requires_authentication,
            "snapshot": self.snapshot.as_dict() if self.snapshot else None,
            "main_text": (self.content.main_text if self.content and include_text else None),
            "tables": [t.as_dict() for t in (self.content.tables if self.content else [])],
            "tabs_available": self.tabs_available,
            "tabs_read": [t.as_dict() for t in self.tabs_read],
            "views_read": [t.as_dict() for t in self.views_read],
            "documents": [d.as_dict() for d in self.documents],
            "values": {
                name: value.as_dict()
                for name, value in (self.validation.accepted if self.validation else {}).items()
            },
            "validation": self.validation.as_dict() if self.validation else None,
            "unmapped_labels": self.unmapped_labels,
            "chart": self.chart,
            "visual": self.visual,
            "screenshots": self.screenshots,
            "navigation_log": self.navigation_log,
        }


class KaseBrowserAgent:
    """Drives a BrowserSession through the public KASE site."""

    version = "1.0.0"

    def __init__(
        self,
        session: BrowserSession,
        *,
        base_url: str | None = None,
        language: str | None = None,
    ) -> None:
        self.session = session
        self.base_url = (base_url or settings.KASE_WEBSITE_URL).rstrip("/")
        self.language = (language or settings.KASE_LANGUAGE).lower()
        self.extractor = PageExtractor(session)
        self.tabs = KaseTabExplorer(session)
        self.validator = DataValidator()
        self.visual = KaseVisualAnalyzer()

    # -- helpers -----------------------------------------------------------

    def url_for(self, key: str, **kwargs) -> str:
        path = PATHS[key].format(lang=self.language, **kwargs)
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def confirms_domain(self, url: str | None) -> bool:
        """Refuse to treat anything but the official host as a KASE source."""
        host = (url or "").split("//")[-1].split("/")[0].lower()
        official = self.base_url.split("//")[-1].split("/")[0].lower()
        return host == official or host.endswith("." + official)

    async def _goto(self, url: str, *, min_chars: int = 400) -> dict:
        """Navigate, wait for the SPA to render, then report any wall hit."""
        result = await self.session.open_url(url)
        if not result.ok:
            return {"ok": False, "error": result.error, "status": result.status}
        await self.session.wait_for_page(state="networkidle", timeout_ms=20_000)
        await self.session.wait_for_content(min_chars=min_chars, timeout_ms=20_000)
        blocks = await self.session.detect_blocks()
        if blocks["browser_blocked_by_captcha"]:
            return {"ok": False, "status": BrowserStatus.BLOCKED_BY_CAPTCHA.value, **blocks}
        if blocks["requires_authentication"]:
            return {"ok": False, "status": BrowserStatus.REQUIRES_AUTHENTICATION.value, **blocks}
        if blocks["blocked"]:
            return {
                "ok": False,
                "status": BrowserStatus.UNAVAILABLE.value,
                "error": "the site refused the request",
                **blocks,
            }
        if self.language:
            await self.session.ensure_language(self.language)
        return {"ok": True, "status": BrowserStatus.OK.value, **blocks}

    # -- §25 catalogue -----------------------------------------------------

    async def discover_catalog(
        self,
        *,
        limit: int | None = None,
        use_cache: bool = True,
        budget: RuntimeBudget | None = None,
    ) -> CatalogResult:
        """Read the official bond catalogue. The list is never hardcoded."""
        url = self.url_for("catalog")
        if use_cache:
            cached = page_cache.get(url, "catalog")
            if cached is not None:
                result: CatalogResult = cached.value
                logger.info("catalog served from browser cache (%d entries)", len(result.entries))
                return result

        budget = budget or RuntimeBudget()
        navigation = await self._goto(url, min_chars=800)
        if not navigation["ok"]:
            return CatalogResult(
                status=navigation.get("status", BrowserStatus.ERROR.value),
                error=navigation.get("error"),
            )

        # The catalogue table is the one whose headers include an instrument
        # code column - found by header text, not by position or class.
        content = await self.extractor.extract(section="catalog", with_documents=False)
        best_index, best_headers = _pick_catalog_table(content)
        if best_index is None:
            return CatalogResult(
                snapshot=content.snapshot,
                status=BrowserStatus.NOT_FOUND.value,
                error="no table with an instrument-code column was found on the catalogue page",
            )

        table = await extract_dynamic_table(
            self.session,
            section="catalog",
            table_index=best_index,
            max_rows=limit or settings.BROWSER_MAX_ROWS,
            max_runtime_s=budget.remaining,
        )
        headers = table.headers or best_headers
        mapping = {i: catalog_field_for(h) for i, h in enumerate(headers)}

        # Row -> official instrument URL, taken from the row's own links.
        href_by_ticker = await self._catalog_links()

        entries: list[CatalogEntry] = []
        for row in table.rows:
            record = {headers[i] if i < len(headers) else f"col_{i}": cell for i, cell in enumerate(row)}
            ticker = None
            isin = None
            issuer = None
            currency = None
            for index, cell in enumerate(row):
                name = mapping.get(index)
                if name == "ticker" and ticker is None:
                    ticker = normalize_ticker(cell)
                elif name == "isin" and isin is None:
                    isin = normalize_isin(cell)
                elif name == "issuer_name" and issuer is None:
                    issuer = cell.strip() or None
                elif name == "currency" and currency is None:
                    currency = cell.strip().upper()[:8] or None
            if not ticker:
                continue
            entries.append(
                CatalogEntry(
                    ticker=ticker,
                    url=href_by_ticker.get(ticker.casefold())
                    or self.url_for("bond", ticker=ticker),
                    isin=isin,
                    issuer_name=issuer,
                    currency=currency,
                    raw=record,
                )
            )
            if limit and len(entries) >= limit:
                break

        result = CatalogResult(
            entries=entries,
            snapshot=content.snapshot,
            truncated=table.truncated,
        )
        page_cache.put(url, result, kind="catalog", content_hash=content.snapshot.html_hash)
        logger.info("catalog discovered: %d instruments from %s", len(entries), url)
        return result

    async def _catalog_links(self) -> dict[str, str]:
        """Official per-instrument URLs, harvested from the catalogue's links."""
        links = await self.session.get_links()
        out: dict[str, str] = {}
        for link in links:
            href = link.get("href") or ""
            if "/bonds/" not in href and "/instruments/" not in href:
                continue
            slug = href.rstrip("/").rsplit("/", 1)[-1]
            ticker = normalize_ticker(slug)
            if ticker and self.confirms_domain(href):
                out.setdefault(ticker.casefold(), href)
            text_ticker = normalize_ticker(link.get("text") or "")
            if text_ticker and self.confirms_domain(href):
                out.setdefault(text_ticker.casefold(), href)
        return out

    # -- §26 instrument page -----------------------------------------------

    async def open_bond(
        self,
        ticker: str,
        *,
        url: str | None = None,
        sections: list[str] | None = None,
        max_tabs: int = 4,
        with_screenshot: bool = True,
        with_visual: bool = False,
        with_chart: bool = True,
        with_views: bool = True,
        use_cache: bool = True,
        budget: RuntimeBudget | None = None,
    ) -> BondPageResult:
        """The full instrument flow: open, verify, read, walk tabs, validate."""
        target = url or self.url_for("bond", ticker=ticker)
        result = BondPageResult(ticker=ticker, url=target)

        if not self.confirms_domain(target):
            result.status = BrowserStatus.ERROR.value
            result.error = f"refusing to browse a non-KASE host: {target}"
            return result

        if use_cache:
            cached = page_cache.get(target, "bond")
            if cached is not None:
                logger.info("bond page served from browser cache: %s", ticker)
                return cached.value

        budget = budget or RuntimeBudget()
        navigation = await self._goto(target, min_chars=300)
        result.browser_blocked_by_captcha = bool(navigation.get("browser_blocked_by_captcha"))
        result.requires_authentication = bool(navigation.get("requires_authentication"))
        if not navigation["ok"]:
            result.status = navigation.get("status", BrowserStatus.ERROR.value)
            result.error = navigation.get("error") or result.status
            result.navigation_log = self.session.navigation_log_dicts()
            return result

        # Read the landing tab before touching anything.
        content = await self.extractor.extract(section="overview")
        result.content = content
        result.snapshot = content.snapshot
        result.url = content.snapshot.url

        # §26.3-4: confirm this really is the instrument we asked for, by
        # ticker and, when the page states one, by ISIN.
        result.identity_confirmed = _identity_matches(ticker, content)
        if not result.identity_confirmed:
            result.status = BrowserStatus.NOT_FOUND.value
            result.error = (
                f"the page at {result.url} does not identify itself as {ticker}"
            )
            result.navigation_log = self.session.navigation_log_dicts()
            return result

        # §7: text hidden behind "show more"/accordions belongs to the page.
        await self.tabs.expand_hidden_text()

        available, read = await self.tabs.explore(
            wanted_sections=sections or BOND_SECTIONS,
            max_tabs=max_tabs,
            screenshot=False,
            budget=budget,
        )
        result.tabs_available = available
        result.tabs_read = read

        # The trade table is rendered three ways behind toggles a user clicks:
        # clean price, dirty price and yield. Reading only the default view
        # would mean never seeing the yields the page actually publishes.
        if with_views and budget.check("price views"):
            result.views_read = await self.tabs.explore_views(budget=budget)

        # Documents from the landing page and from every tab visited.
        documents = {d.url: d for d in content.documents}
        for tab in read + list(result.views_read):
            for document in tab.documents:
                documents.setdefault(document.url, document)
        result.documents = list(documents.values())

        # §13: structured chart data first; pixels only if there is none.
        if with_chart and budget.check("chart probe"):
            chart = await find_chart_data_in_dom(self.session)
            tooltips = []
            if not chart.get("series") and not chart.get("accessible_table"):
                tooltips = await read_chart_tooltips(self.session, samples=4)
            chart["tooltips"] = tooltips
            chart["precise_values_available"] = bool(
                chart.get("series") or chart.get("accessible_table") or tooltips
            )
            result.chart = chart

        if with_screenshot and settings.BROWSER_STORE_SCREENSHOTS:
            shot = await self.session.take_screenshot(name=f"bond_{ticker}")
            if shot.ok:
                result.screenshots.append(shot.value)

        # §53: visual analysis only when it adds something the DOM did not.
        if with_visual and result.screenshots and not result.chart.get("precise_values_available"):
            analysis = await self.visual.analyze_file(
                result.screenshots[0],
                page_context=f"Страница облигации {ticker} на KASE: {result.url}",
                task=(
                    "Опиши структуру страницы, видимые подписи и график. "
                    "Не называй числовые значения, которых нет в виде текста."
                ),
            )
            result.visual = analysis.as_dict()

        # Candidate values from every label/value pair we saw, on the landing
        # page and inside each tab. Duplicates are kept on purpose - the
        # validator uses them to cross-check (§30).
        candidates: list[ExtractedValue] = []
        unmapped: dict[str, str] = {}
        values, leftover = values_from_labels(
            content.label_values, source=content.snapshot.source_ref("overview")
        )
        candidates.extend(values)
        unmapped.update(leftover)

        for tab in read:
            pairs = _pairs_from_tab(tab)
            if not pairs:
                continue
            source = content.snapshot.source_ref(tab.tab_name)
            values, leftover = values_from_labels(pairs, source=source)
            candidates.extend(values)
            unmapped.update(leftover)

        candidates.extend(_header_candidates(ticker, content))
        result.candidates = candidates
        result.unmapped_labels = unmapped
        result.validation = self.validator.validate(candidates)
        result.navigation_log = self.session.navigation_log_dicts()

        page_cache.put(target, result, kind="bond", content_hash=content.snapshot.html_hash)
        logger.info(
            "bond flow finished ticker=%s tabs_read=%d values=%d documents=%d",
            ticker, len(read), len(result.validation.accepted), len(result.documents),
        )
        return result

    # -- §27 search --------------------------------------------------------

    async def search(self, identifier: str, *, max_results: int = 10) -> list[CatalogEntry]:
        """Find an instrument by ticker or ISIN using the site's own search.

        The caller is expected to have checked the local database first (§27);
        this is the fallback for something we have never seen.
        """
        url = self.url_for("instruments")
        navigation = await self._goto(url, min_chars=300)
        if not navigation["ok"]:
            logger.info("search page unavailable: %s", navigation)
            return []

        filled = await self.session.fill(
            LocatorTarget(role="searchbox", label="Поиск", css="input[type=search], input[type=text]"),
            identifier,
        )
        if filled.ok:
            await self.session.press("Enter")
            await self.session.wait_for_page(state="networkidle", timeout_ms=15_000)
            await self.session.page.wait_for_timeout(1_200)

        entries: list[CatalogEntry] = []
        needle = identifier.casefold()
        for link in await self.session.get_links():
            href = link.get("href") or ""
            if "/bonds/" not in href or not self.confirms_domain(href):
                continue
            slug = href.rstrip("/").rsplit("/", 1)[-1]
            ticker = normalize_ticker(slug)
            if not ticker:
                continue
            text = (link.get("text") or "").casefold()
            if needle not in ticker.casefold() and needle not in text:
                continue
            entries.append(CatalogEntry(ticker=ticker, url=href))
            if len(entries) >= max_results:
                break

        if entries:
            return entries

        # The public search UI did not help; the catalogue still can, and it is
        # the same public data.
        catalog = await self.discover_catalog()
        return [
            entry
            for entry in catalog.entries
            if needle in entry.ticker.casefold()
            or (entry.isin and needle in entry.isin.casefold())
        ][:max_results]


def _pick_catalog_table(content: PageContent) -> tuple[int | None, list[str]]:
    """Choose the instrument table by what its headers say, not where it sits."""
    best: tuple[int, int, list[str]] | None = None
    for index, table in enumerate(content.tables):
        fields = [catalog_field_for(h) for h in table.headers]
        if "ticker" not in fields:
            continue
        score = sum(1 for f in fields if f) * 100 + len(table.rows)
        if best is None or score > best[1]:
            best = (index, score, table.headers)
    if best is None:
        return None, []
    return best[0], best[2]


def _identity_matches(ticker: str, content: PageContent) -> bool:
    """Does this page actually belong to the instrument we asked for? (§26)"""
    wanted = ticker.casefold()
    if wanted in (content.snapshot.url or "").casefold():
        return True
    if wanted in (content.snapshot.page_title or "").casefold():
        return True
    head = content.main_text[:1500].casefold()
    return wanted in head


def _pairs_from_tab(tab: TabResult) -> dict[str, str]:
    from app.browser.extractors.text import label_value_pairs

    pairs = dict(label_value_pairs(tab.text))
    for table in tab.tables:
        for row in table.rows:
            if len(row) == 2 and row[0].strip() and row[1].strip():
                pairs.setdefault(row[0].strip(), row[1].strip())
    return pairs


#: Headings that mark the end of a KASE instrument page's identity block and
#: the start of its tabbed content.
_IDENTITY_STOP_WORDS = (
    "торги",
    "характеристики",
    "сводные данные",
    "trades",
    "characteristic",
)


def _identity_block(text: str, *, max_chars: int = 1200) -> str:
    """The part of the page that describes the instrument itself.

    Everything above the tab strip belongs to this bond. Below it the page
    mixes in trade tables and the issuer's other securities.
    """
    window = text[:max_chars]
    cut = len(window)
    for line_start, line in _iter_lines(window):
        folded = line.strip().casefold()
        if folded and any(folded.startswith(word) for word in _IDENTITY_STOP_WORDS):
            cut = min(cut, line_start)
            break
    return window[:cut]


def _iter_lines(text: str):
    position = 0
    for line in text.splitlines(keepends=True):
        yield position, line
        position += len(line)


def _header_candidates(ticker: str, content: PageContent) -> list[ExtractedValue]:
    """A few facts KASE prints in the page header rather than in a table."""
    source = content.snapshot.source_ref("header")
    out: list[ExtractedValue] = [
        make_value(
            "ticker",
            ticker,
            normalize_ticker(ticker),
            method=ExtractionMethod.DOM.value,
            source=source,
            label="ticker requested and confirmed on page",
        )
    ]
    # Only the identity block above the tab strip belongs to *this* bond.
    # Further down the page KASE lists the issuer's other securities, and
    # reading an ISIN out of that list would label the bond with a sibling's
    # identifier - which is exactly what used to happen.
    identity_block = _identity_block(content.main_text)
    isins = find_isins(identity_block)
    if len(isins) == 1:
        out.append(
            make_value(
                "isin",
                isins[0],
                isins[0],
                method=ExtractionMethod.DOM.value,
                source=source,
                label="ISIN в заголовке страницы",
            )
        )
    elif len(isins) > 1:
        # Ambiguous header: say nothing and let the characteristics table,
        # which is labelled, supply the ISIN instead.
        logger.info(
            "header holds %d ISINs for %s; deferring to the labelled table",
            len(isins), ticker,
        )
    for line in content.main_text.splitlines()[:60]:
        folded = line.casefold()
        if "количество дней до погашения" in folded:
            days = parse_number(line.split(":")[-1])
            if days is not None:
                out.append(
                    make_value(
                        "days_to_maturity",
                        line.strip(),
                        days,
                        method=ExtractionMethod.DOM.value,
                        source=source,
                        label="Количество дней до погашения",
                        unit="days",
                        confidence=METHOD_CONFIDENCE[ExtractionMethod.DOM.value],
                    )
                )
    return out


# -- convenience -------------------------------------------------------------


class KaseBrowsingContext:
    """``async with`` wrapper that always closes its session."""

    def __init__(self, service: BrowserService | None = None, *, label: str | None = None):
        self._service = service or browser_service
        self._label = label
        self.session: BrowserSession | None = None
        self.agent: KaseBrowserAgent | None = None

    async def __aenter__(self) -> KaseBrowserAgent:
        self.session = await self._service.new_session(label=self._label)
        self.agent = KaseBrowserAgent(self.session)
        return self.agent

    async def __aexit__(self, *_exc) -> None:
        if self.session is not None:
            await self._service.close_session(self.session)
            self.session = None
