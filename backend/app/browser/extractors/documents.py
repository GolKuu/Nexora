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


#: Path segments that mark a link as a publication rather than navigation. Only
#: KASE's own hosts are followed - an issuer's press release on a third-party
#: site is not a KASE source and is not treated as one.
PUBLICATION_SEGMENTS = (
    "/news", "/announcements", "/announce", "/press", "/publications",
    "/emitters/show", "/disclosure", "/hab", "/events",
)
KASE_HOSTS = {"kase.kz", "www.kase.kz"}


async def extract_publication_links(
    session: BrowserSession, *, section: str | None = None
) -> list[dict]:
    """Public news and disclosure links currently rendered on the page.

    Returns raw link dictionaries rather than a typed record: what counts as an
    issuer publication is a decision for the backfill parser, which knows the
    ticker, not for the browser layer, which only knows the DOM.

    Titles are the link's own text and dates come from the page around it. A
    link whose date the page never states keeps ``publication_date=None`` - the
    year in a URL is not a publication date.
    """
    page_url = await session.get_current_url()
    entries = await session.page.evaluate(
        """() => [...document.querySelectorAll('a[href]')].map((a) => {
            const container = a.closest('li, tr, article, .card, .item, .news-item');
            return {
                href: a.href,
                text: (a.innerText || '').trim().slice(0, 400),
                title: a.getAttribute('title'),
                time: (() => {
                    const el = container ? container.querySelector('time') : null;
                    return el ? (el.getAttribute('datetime') || el.innerText || '').trim() : '';
                })(),
                context: container ? (container.innerText || '').trim().slice(0, 400) : '',
            };
        })"""
    )

    seen: set[str] = set()
    links: list[dict] = []
    for entry in entries:
        url = entry["href"]
        parsed = urlparse(url)
        if parsed.hostname not in KASE_HOSTS:
            continue
        path = parsed.path.lower()
        if not any(segment in path for segment in PUBLICATION_SEGMENTS):
            continue
        if document_type_for(url) is not None:
            continue  # a downloadable file: that is a document, not an article
        if url in seen:
            continue
        title = (entry["text"] or entry.get("title") or "").strip()
        if len(title) < 12:
            continue  # navigation chrome ("Новости", "Все") is not a publication
        seen.add(url)
        context = entry.get("context") or ""
        stated = entry.get("time") or ""
        match = _DATE_IN_TEXT.search(stated) or _DATE_IN_TEXT.search(context)
        links.append(
            {
                "url": url,
                "title": title,
                "publication_date": stated.strip() or (match.group(0) if match else None),
                "context": context,
                "source_page": page_url,
                "section": section,
            }
        )
    return links


class KaseDocumentCollector:
    """Find public document links; downloading/parsing remains a later stage."""

    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    async def collect(self, *, section: str | None = None) -> list[DocumentLink]:
        return await extract_documents(self.session, section=section)


__all__ = [
    "DOCUMENT_TYPES",
    "KASE_HOSTS",
    "KaseDocumentCollector",
    "PUBLICATION_SEGMENTS",
    "document_type_for",
    "extract_documents",
    "extract_publication_links",
]
