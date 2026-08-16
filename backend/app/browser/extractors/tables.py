"""HTML table extraction, including tables that arrive in pieces (§9, §10).

A static table is read in one evaluate() call. A dynamic one - paginated, with
a "show more" button, or with virtual scrolling - is walked step by step, and
every walk is bounded four ways at once: ``max_pages``, ``max_scrolls``,
``max_rows`` and ``max_runtime``. When a bound is hit the result is marked
``truncated`` rather than quietly cut short.
"""

from __future__ import annotations

from app.browser.pacing import RuntimeBudget
from app.browser.session import BrowserSession
from app.browser.types import TableData
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Visible text of controls that reveal more rows. Matched case-insensitively
#: against the element's own text, in both site languages.
MORE_LABELS = (
    "показать еще", "показать ещё", "загрузить еще", "загрузить ещё",
    "show more", "load more", "еще", "ещё",
)
NEXT_LABELS = ("следующая", "вперед", "вперёд", "next", "›", "»", "→")

#: One JS pass that reads every table on the page. Header detection tries
#: <thead>, then a first row made only of <th>, then falls back to no headers -
#: it never assumes a particular class name.
_READ_TABLES_JS = """
(maxRows) => {
    const clean = (el) => (el.innerText || el.textContent || '')
        .replace(/\\u00a0/g, ' ').trim();
    return [...document.querySelectorAll('table')].map((t) => {
        let headers = [...t.querySelectorAll('thead th, thead td')].map(clean);
        let bodyRows = [...t.querySelectorAll('tbody tr')];
        if (!bodyRows.length) bodyRows = [...t.querySelectorAll('tr')];
        if (!headers.length && bodyRows.length) {
            const first = bodyRows[0];
            const ths = [...first.querySelectorAll('th')];
            if (ths.length && ths.length === first.children.length) {
                headers = ths.map(clean);
                bodyRows = bodyRows.slice(1);
            }
        }
        const rows = [];
        for (const tr of bodyRows) {
            if (rows.length >= maxRows) break;
            const cells = [...tr.querySelectorAll('td, th')].map(clean);
            if (cells.some((c) => c !== '')) rows.push(cells);
        }
        const caption = t.querySelector('caption');
        return {
            headers,
            rows,
            caption: caption ? clean(caption) : null,
            total_body_rows: bodyRows.length,
        };
    });
}
"""


async def read_tables(session: BrowserSession, *, max_rows: int | None = None) -> list[dict]:
    limit = max_rows or settings.BROWSER_MAX_ROWS
    try:
        return await session.page.evaluate(_READ_TABLES_JS, limit)
    except Exception as exc:
        logger.info("table read failed: %s", exc)
        return []


async def extract_tables(
    session: BrowserSession,
    *,
    section: str | None = None,
    max_rows: int | None = None,
    min_rows: int = 1,
) -> list[TableData]:
    """Every table currently rendered on the page, as structured data."""
    snapshot_ref = (await session.snapshot()).source_ref(section)
    raw = await read_tables(session, max_rows=max_rows)
    limit = max_rows or settings.BROWSER_MAX_ROWS
    out: list[TableData] = []
    for item in raw:
        if len(item["rows"]) < min_rows:
            continue
        out.append(
            TableData(
                headers=item["headers"],
                rows=item["rows"],
                caption=item.get("caption"),
                section=section,
                truncated=item.get("total_body_rows", 0) > len(item["rows"]),
                source=snapshot_ref,
            )
        )
    if any(t.truncated for t in out):
        logger.info("table truncated at max_rows=%s on %s", limit, snapshot_ref.page_url)
    return out


async def _click_first_label(session: BrowserSession, labels: tuple[str, ...]) -> bool:
    """Click the first visible control whose own text matches one of ``labels``.

    The page text is consulted first so that a table with no pagination at all
    costs one read instead of a dozen fruitless locator searches per round.
    """
    page_text = (await session.get_visible_text()).casefold()
    for label in labels:
        if label not in page_text:
            continue
        result = await session.click_text(label, exact=False)
        if result.ok:
            await session.page.wait_for_timeout(1_200)
            return True
    return False


async def extract_dynamic_table(
    session: BrowserSession,
    *,
    section: str | None = None,
    table_index: int = 0,
    max_pages: int | None = None,
    max_scrolls: int | None = None,
    max_rows: int | None = None,
    max_runtime_s: float | None = None,
) -> TableData:
    """Walk a paginated / infinite-scroll / virtualised table to its end or a limit.

    Strategy per round: read the table, try "show more", else try "next page",
    else scroll. Stop as soon as a round produces no new rows - that is what
    prevents the infinite loop the naive version of this always becomes.
    """
    budget = RuntimeBudget(max_runtime_s if max_runtime_s is not None else settings.BROWSER_MAX_RUNTIME_S)
    page_limit = max_pages if max_pages is not None else settings.BROWSER_MAX_PAGES
    scroll_limit = max_scrolls if max_scrolls is not None else settings.BROWSER_MAX_SCROLLS
    row_limit = max_rows if max_rows is not None else settings.BROWSER_MAX_ROWS

    seen: set[tuple[str, ...]] = set()
    rows: list[list[str]] = []
    headers: list[str] = []
    caption: str | None = None
    pages_used = 0
    scrolls_used = 0
    truncated = False
    stalled_rounds = 0

    while True:
        tables = await read_tables(session, max_rows=row_limit)
        if table_index < len(tables):
            item = tables[table_index]
            headers = item["headers"] or headers
            caption = item.get("caption") or caption
            added = 0
            for row in item["rows"]:
                key = tuple(row)
                if key in seen:
                    continue
                if len(rows) >= row_limit:
                    truncated = True
                    break
                seen.add(key)
                rows.append(row)
                added += 1
        else:
            added = 0

        if len(rows) >= row_limit:
            truncated = True
            break
        if not budget.check("next table page"):
            truncated = True
            break
        if pages_used >= page_limit or scrolls_used >= scroll_limit:
            truncated = True
            break

        advanced = False
        if await _click_first_label(session, MORE_LABELS):
            pages_used += 1
            advanced = True
        elif await _click_first_label(session, NEXT_LABELS):
            pages_used += 1
            advanced = True
        else:
            await session.scroll_to_bottom()
            await session.page.wait_for_timeout(700)
            scrolls_used += 1
            advanced = True

        if added == 0:
            stalled_rounds += 1
            # Two rounds with no new rows means the table has nothing left to
            # give - stop instead of scrolling forever.
            if stalled_rounds >= 2:
                break
        else:
            stalled_rounds = 0
        if not advanced:
            break

    snapshot_ref = (await session.snapshot()).source_ref(section)
    logger.info(
        "dynamic table done rows=%d pages=%d scrolls=%d truncated=%s elapsed=%.1fs",
        len(rows), pages_used, scrolls_used, truncated, budget.elapsed,
    )
    return TableData(
        headers=headers,
        rows=rows,
        caption=caption,
        section=section,
        truncated=truncated,
        source=snapshot_ref,
    )


class KaseTableExtractor:
    """Bound table extractor used by the page and tab pipelines."""

    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    async def extract(
        self, *, section: str | None = None, max_rows: int | None = None,
        min_rows: int = 1,
    ) -> list[TableData]:
        return await extract_tables(
            self.session, section=section, max_rows=max_rows, min_rows=min_rows
        )

    async def extract_dynamic(self, **kwargs) -> TableData:
        return await extract_dynamic_table(self.session, **kwargs)
