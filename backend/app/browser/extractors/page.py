"""PageExtractor - one page, everything readable on it.

Composes the specialised extractors into a single pass and stamps every piece
with the same ``SourceRef`` so that anything downstream can answer "where did
this come from?" without guesswork (§8).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.browser.extractors.documents import KaseDocumentCollector
from app.browser.extractors.tables import KaseTableExtractor
from app.browser.extractors.text import KaseTextExtractor, label_value_pairs
from app.browser.session import BrowserSession
from app.browser.types import DocumentLink, PageSnapshot, TableData


@dataclass(slots=True)
class PageContent:
    snapshot: PageSnapshot
    main_text: str
    raw_text: str
    tables: list[TableData] = field(default_factory=list)
    documents: list[DocumentLink] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    label_values: dict[str, str] = field(default_factory=dict)
    text_stats: dict = field(default_factory=dict)

    def as_dict(self, *, include_raw_text: bool = False) -> dict:
        payload = {
            "snapshot": self.snapshot.as_dict(),
            "main_text": self.main_text,
            "tables": [t.as_dict() for t in self.tables],
            "documents": [d.as_dict() for d in self.documents],
            "label_values": self.label_values,
            "text_stats": self.text_stats,
            "link_count": len(self.links),
        }
        if include_raw_text:
            payload["raw_text"] = self.raw_text
        return payload


class KaseDomExtractor:
    """Read rendered DOM text, labels, links, tables and document references."""

    version = "1.0.0"

    def __init__(self, session: BrowserSession) -> None:
        self.session = session
        self.text_extractor = KaseTextExtractor()
        self.table_extractor = KaseTableExtractor(session)
        self.document_collector = KaseDocumentCollector(session)

    async def extract(
        self,
        *,
        section: str | None = None,
        with_tables: bool = True,
        with_documents: bool = True,
        keep_html: bool = False,
    ) -> PageContent:
        snapshot = await self.session.snapshot(section=section, keep_html=keep_html)
        text = self.text_extractor.extract_object(snapshot.visible_text)
        tables = (
            await self.table_extractor.extract(section=section)
            if with_tables else []
        )
        documents = (
            await self.document_collector.collect(section=section)
            if with_documents else []
        )
        links = await self.session.get_links()

        # Label/value pairs come from two independent readings of the page -
        # the two-column tables and the flat text. Merging them means a field
        # that only one of them sees is still captured.
        pairs = dict(label_value_pairs(snapshot.visible_text))
        for table in tables:
            for row in table.rows:
                if len(row) == 2 and row[0] and row[1]:
                    pairs.setdefault(row[0].strip(), row[1].strip())

        return PageContent(
            snapshot=snapshot,
            main_text=text.main_text,
            raw_text=text.raw_text,
            tables=tables,
            documents=documents,
            links=links,
            label_values=pairs,
            text_stats={
                "lines_total": text.lines_total,
                "lines_kept": text.lines_kept,
                "removed": text.removed_reasons,
            },
        )


# Historical public name retained for existing imports.
PageExtractor = KaseDomExtractor
