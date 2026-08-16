"""Official documents linked from a page (§16, §17).

The browser agent's job ends at "here is the official URL and what the page
said about it". Downloading and parsing a prospectus or an IFRS statement is
the document pipeline's job - a screenshot of a PDF is not an analysis.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.browser.session import BrowserSession
from app.browser.types import DocumentLink

#: extension -> document_type
DOCUMENT_TYPES = {
    ".pdf": "pdf",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".docx": "docx",
    ".doc": "doc",
    ".csv": "csv",
    ".zip": "archive",
    ".rtf": "rtf",
}

_DATE_IN_TEXT = re.compile(r"\b(\d{2}[.\-/]\d{2}[.\-/]\d{2,4})\b")
_YEAR_IN_TEXT = re.compile(r"\b(19|20)\d{2}\b")


def document_type_for(url: str) -> str | None:
    path = urlparse(url).path.lower()
    for extension, kind in DOCUMENT_TYPES.items():
        if path.endswith(extension):
            return kind
    return None


async def extract_documents(
    session: BrowserSession, *, section: str | None = None
) -> list[DocumentLink]:
    """Every downloadable official document currently linked on the page.

    Publication date is taken from the link's own text or its nearest
    surrounding text when the page states one - it is never inferred from the
    file name alone.
    """
    page_url = await session.get_current_url()
    entries = await session.page.evaluate(
        """() => [...document.querySelectorAll('a[href]')].map((a) => {
            const container = a.closest('li, tr, .card, .item, section, div');
            return {
                href: a.href,
                text: (a.innerText || '').trim().slice(0, 300),
                title: a.getAttribute('title'),
                download: a.getAttribute('download'),
                context: container ? (container.innerText || '').trim().slice(0, 300) : '',
            };
        })"""
    )

    seen: set[str] = set()
    documents: list[DocumentLink] = []
    for entry in entries:
        url = entry["href"]
        kind = document_type_for(url)
        if kind is None and not entry.get("download"):
            continue
        if url in seen:
            continue
        seen.add(url)
        name = (
            entry["text"]
            or entry.get("title")
            or urlparse(url).path.rsplit("/", 1)[-1]
        )
        context = entry.get("context") or entry["text"]
        published = None
        match = _DATE_IN_TEXT.search(context) or _YEAR_IN_TEXT.search(context)
        if match:
            published = match.group(0)
        documents.append(
            DocumentLink(
                url=url,
                name=name.strip(),
                document_type=kind or "unknown",
                source_page=page_url,
                publication_date=published,
                section=section,
            )
        )
    return documents


class KaseDocumentCollector:
    """Find public document links; downloading/parsing remains a later stage."""

    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    async def collect(self, *, section: str | None = None) -> list[DocumentLink]:
        return await extract_documents(self.session, section=section)
