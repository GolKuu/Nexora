"""KaseTabExplorer - discover the page's own sections and read them (§5, §35).

Tab names are *discovered*, never hardcoded. The known-section vocabulary below
is only used to rank which discovered tabs are worth opening; a tab whose name
we have never seen is still visible to the caller and can still be opened.

Each tab is opened once. After the click the content is compared with what was
on screen before: an unchanged page means the click did nothing, and that is
reported rather than dressed up as an empty section.
"""

from __future__ import annotations

import re

from app.browser.extractors.documents import extract_documents
from app.browser.extractors.tables import extract_tables
from app.browser.extractors.text import KaseTextExtractor
from app.browser.locators import LocatorTarget, find_all_text
from app.browser.pacing import RuntimeBudget
from app.browser.session import BrowserSession
from app.browser.types import BrowserStatus, TabResult
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Sections we care about, with the words that identify them in RU/KZ/EN. Used
#: for *ranking*, not for finding: discovery is generic.
SECTION_VOCABULARY: dict[str, tuple[str, ...]] = {
    "trades": ("торги", "сделки", "trades", "trading"),
    "characteristics": ("характеристик", "параметры", "выпуск", "characteristic", "issue"),
    "documents": ("документ", "проспект", "document", "prospectus"),
    "issuer": ("эмитент", "issuer", "компания"),
    "financials": ("отчетность", "отчётность", "финанс", "financial", "reporting"),
    "news": ("новост", "news"),
    "payments": ("выплат", "купон", "payment", "coupon"),
    "history": ("истори", "history", "архив"),
    "related": ("связанн", "другие ценные бумаги", "related", "инструменты"),
    "disclosure": ("раскрытие", "disclosure"),
    "securities": ("ценные бумаги", "securities"),
}

#: Toggles *inside* a tab that swap which numbers the same table shows. On a
#: KASE instrument page the trade table is rendered three ways, and only the
#: first is visible by default, so a reader that never clicks these sees
#: clean prices and never learns the yields.
VIEW_VOCABULARY: dict[str, tuple[str, ...]] = {
    "clean_price": ("чистая цена", "clean price", "таза баға"),
    "dirty_price": ("грязная цена", "dirty price", "лас баға"),
    "yield": ("доходность", "yield", "кірістілік"),
}


def classify_view(name: str | None) -> str | None:
    """Which price view a toggle switches to, or ``None`` if it is not one."""
    folded = _WS.sub(" ", (name or "")).strip().casefold()
    if not folded:
        return None
    for view, words in VIEW_VOCABULARY.items():
        if any(word in folded for word in words):
            return view
    return None


#: CSS hints that merely *widen* discovery. Losing them costs recall, not
#: correctness - ARIA roles and tab-ish class names are found regardless.
TAB_CSS_HINTS = [
    "[role=tab]",
    ".tab",
    ".tabs a",
    ".tabs button",
    ".nav-tabs a",
    "[class*='tab'][class*='item']",
    "[data-tab]",
]

#: Expanders that hide text behind one click (§7).
EXPAND_LABELS = (
    "подробнее", "показать", "просмотр графика", "развернуть", "читать далее",
    "show", "more details", "expand",
)

_WS = re.compile(r"\s+")


def classify(tab_name: str) -> str | None:
    folded = tab_name.casefold()
    for section, words in SECTION_VOCABULARY.items():
        if any(word in folded for word in words):
            return section
    return None


class KaseTabExplorer:
    """Finds the tabs a page actually has and reads the ones asked for."""

    version = "1.0.0"

    def __init__(self, session: BrowserSession) -> None:
        self.session = session
        self.text_extractor = KaseTextExtractor()

    async def discover(self) -> list[dict]:
        """Every tab-like control on the page, with its detected section."""
        try:
            raw = await find_all_text(self.session.page, TAB_CSS_HINTS)
        except Exception as exc:
            logger.info("tab discovery failed: %s", exc)
            return []
        tabs: list[dict] = []
        for item in raw:
            name = _WS.sub(" ", item["text"]).strip()
            if not name or len(name) < 2:
                continue
            tabs.append(
                {
                    "tab_name": name,
                    "section": classify(name),
                    "role": item.get("role"),
                    "tag": item.get("tag"),
                    "href": item.get("href"),
                    "active": bool(item.get("active")),
                }
            )
        return tabs

    async def expand_hidden_text(self, *, limit: int = 4) -> list[str]:
        """Open accordions/"show more" controls so their text is readable (§7).

        Every one of these is optional, so the page text is checked first: a
        label that is not on the page is not worth a locator search.
        """
        page_text = (await self.session.get_visible_text()).casefold()
        opened: list[str] = []
        for label in EXPAND_LABELS:
            if len(opened) >= limit:
                break
            if label not in page_text:
                continue
            result = await self.session.click_text(label, exact=False)
            if result.ok:
                await self.session.page.wait_for_timeout(800)
                opened.append(label)
        return opened

    async def open_tab(
        self,
        tab: dict,
        *,
        with_tables: bool = True,
        with_documents: bool = True,
        screenshot: bool = False,
    ) -> TabResult:
        """Click one tab and read what it reveals."""
        name = tab["tab_name"]
        before_text = await self.session.get_visible_text()
        result = TabResult(tab_name=name, url=await self.session.get_current_url())

        click = await self.session.click(LocatorTarget(text=name, exact=True))
        if not click.ok:
            click = await self.session.click(LocatorTarget(text=name, exact=False))
        if not click.ok:
            result.status = BrowserStatus.NOT_FOUND.value
            result.error = click.error
            return result

        await self.session.wait_for_page(state="networkidle", timeout_ms=8_000)
        await self.session.page.wait_for_timeout(600)

        after_text = await self.session.get_visible_text()
        result.url = await self.session.get_current_url()
        result.changed_content = _WS.sub(" ", after_text) != _WS.sub(" ", before_text)
        result.text = self.text_extractor.extract_object(after_text).main_text
        if not result.changed_content:
            result.error = "content did not change after the click"

        if with_tables:
            result.tables = await extract_tables(self.session, section=name)
        if with_documents:
            result.documents = await extract_documents(self.session, section=name)
        result.links = [
            link["href"] for link in await self.session.get_links() if link.get("href")
        ]
        if screenshot and settings.BROWSER_STORE_SCREENSHOTS:
            shot = await self.session.take_screenshot(name=f"tab_{classify(name) or 'other'}")
            result.screenshot_path = shot.value if shot.ok else None
        return result

    async def explore_views(
        self,
        *,
        max_views: int = 3,
        budget: RuntimeBudget | None = None,
    ) -> list[TabResult]:
        """Click the price-view toggles inside the current tab and read each.

        These are what a user clicks to switch the same trade table between
        clean prices, dirty prices and yields. They are not sections, so the
        section-based explorer skips them; without this the agent only ever
        sees the default view.
        """
        budget = budget or RuntimeBudget()
        discovered = await self.discover()
        views = []
        seen: set[str] = set()
        for tab in discovered:
            view = classify_view(tab.get("tab_name"))
            if view is None or view in seen:
                continue
            seen.add(view)
            views.append((view, tab))

        results: list[TabResult] = []
        for view, tab in views[:max_views]:
            if not budget.check(f"view {view}"):
                break
            result = await self.open_tab(tab, with_documents=False)
            # Label the result with the view it represents, so a caller can
            # tell a yield column from a price column later.
            result.view = view
            results.append(result)
        return results

    async def explore(
        self,
        *,
        wanted_sections: list[str] | None = None,
        max_tabs: int = 6,
        screenshot: bool = False,
        budget: RuntimeBudget | None = None,
    ) -> tuple[list[dict], list[TabResult]]:
        """Discover tabs, then open the relevant ones exactly once each."""
        budget = budget or RuntimeBudget()
        discovered = await self.discover()
        if not discovered:
            return [], []

        def rank(tab: dict) -> tuple[int, int]:
            section = tab["section"]
            if wanted_sections and section in wanted_sections:
                return (0, wanted_sections.index(section))
            if section:
                return (1, 0)
            return (2, 0)

        candidates = sorted(discovered, key=rank)
        if wanted_sections:
            candidates = [t for t in candidates if t["section"] in wanted_sections] or candidates

        results: list[TabResult] = []
        visited: set[str] = set()
        for tab in candidates:
            if len(results) >= max_tabs:
                break
            if not budget.check(f"tab {tab['tab_name']}"):
                break
            key = tab["tab_name"].casefold()
            if key in visited:
                continue
            visited.add(key)
            results.append(await self.open_tab(tab, screenshot=screenshot))
        return discovered, results
