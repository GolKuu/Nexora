"""Value objects the browser layer passes around.

Two rules shape every type here:

* nothing leaves the browser without its provenance (where it came from, when,
  which session, which extractor version);
* nothing leaves the browser claiming more certainty than the extraction method
  actually earned - which is why ``ExtractionMethod`` and ``confidence`` travel
  with every single value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

#: Bump when the extraction logic changes in a way that alters output.
EXTRACTOR_VERSION = "1.0.0"


class ExtractionMethod(StrEnum):
    """How a value was obtained. Ordered from most to least trustworthy."""

    DOM = "dom"
    TABLE = "table"
    TOOLTIP = "tooltip"
    DOCUMENT = "document"
    VISUAL = "visual"


#: Default confidence per method. Visual interpretation is deliberately low: it
#: is evidence about shape and trend, not about numbers.
METHOD_CONFIDENCE: dict[str, float] = {
    ExtractionMethod.DOM.value: 0.99,
    ExtractionMethod.TABLE.value: 0.97,
    ExtractionMethod.TOOLTIP.value: 0.90,
    ExtractionMethod.DOCUMENT.value: 0.85,
    ExtractionMethod.VISUAL.value: 0.35,
}


class BrowserStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    BLOCKED_BY_CAPTCHA = "blocked_by_captcha"
    REQUIRES_AUTHENTICATION = "requires_authentication"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def jsonable(value: Any) -> Any:
    """Make a normalized value safe for JSON storage and transport.

    ``as_dict`` is the boundary where an ``ExtractedValue`` becomes a snapshot
    row or an API response, and both are JSON. Dates therefore become ISO
    strings here - the typed object stays available on ``.normalized`` for the
    code that writes it into a Date column.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return value


@dataclass(slots=True)
class SourceRef:
    """Where a piece of text or a number physically came from (§8)."""

    page_url: str
    page_title: str | None = None
    section: str | None = None
    fetched_at: datetime = field(default_factory=utcnow)
    source_timestamp: datetime | None = None
    browser_session_id: str | None = None
    extractor_version: str = EXTRACTOR_VERSION

    def as_dict(self) -> dict:
        return {
            "page_url": self.page_url,
            "page_title": self.page_title,
            "section": self.section,
            "fetched_at": self.fetched_at.isoformat(),
            "source_timestamp": self.source_timestamp.isoformat()
            if self.source_timestamp
            else None,
            "browser_session_id": self.browser_session_id,
            "extractor_version": self.extractor_version,
        }


@dataclass(slots=True)
class ExtractedValue:
    """One field read off a page.

    ``raw`` is what the site literally showed. ``normalized`` is our internal
    representation. The raw form is never discarded (§44).
    """

    field: str
    raw: str | None
    normalized: Any = None
    unit: str | None = None
    method: str = ExtractionMethod.DOM.value
    confidence: float = METHOD_CONFIDENCE[ExtractionMethod.DOM.value]
    source: SourceRef | None = None
    label: str | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "label": self.label,
            "raw_value": self.raw,
            "normalized_value": jsonable(self.normalized),
            "unit": self.unit,
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "warnings": self.warnings,
            "source": self.source.as_dict() if self.source else None,
        }


@dataclass(slots=True)
class TableData:
    """A DOM table turned into rows of ``{header: cell}``."""

    headers: list[str]
    rows: list[list[str]]
    caption: str | None = None
    section: str | None = None
    truncated: bool = False
    source: SourceRef | None = None

    @property
    def records(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for row in self.rows:
            if self.headers:
                out.append(
                    {
                        (self.headers[i] if i < len(self.headers) else f"col_{i}"): cell
                        for i, cell in enumerate(row)
                    }
                )
            else:
                out.append({f"col_{i}": cell for i, cell in enumerate(row)})
        return out

    def as_dict(self) -> dict:
        return {
            "caption": self.caption,
            "section": self.section,
            "headers": self.headers,
            "rows": self.rows,
            "records": self.records,
            "row_count": len(self.rows),
            "truncated": self.truncated,
            "source": self.source.as_dict() if self.source else None,
        }


@dataclass(slots=True)
class DocumentLink:
    """An official document found on a page (§16). Not downloaded here."""

    url: str
    name: str
    document_type: str
    source_page: str
    publication_date: str | None = None
    section: str | None = None

    def as_dict(self) -> dict:
        return {
            "document_url": self.url,
            "document_name": self.name,
            "document_type": self.document_type,
            "publication_date": self.publication_date,
            "source_page": self.source_page,
            "section": self.section,
        }


@dataclass(slots=True)
class VisualAnalysis:
    """Output of KaseVisualAnalyzer. Qualitative by construction (§33)."""

    description: str = ""
    visible_labels: list[str] = field(default_factory=list)
    chart_present: bool = False
    table_present: bool = False
    warnings: list[str] = field(default_factory=list)
    qualitative_findings: list[str] = field(default_factory=list)
    confidence: float = 0.0
    screenshot_path: str | None = None
    available: bool = True
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "description": self.description,
            "visible_labels": self.visible_labels,
            "chart_present": self.chart_present,
            "table_present": self.table_present,
            "warnings": self.warnings,
            "qualitative_findings": self.qualitative_findings,
            "confidence": round(self.confidence, 3),
            "screenshot_path": self.screenshot_path,
            "available": self.available,
            "reason": self.reason,
        }


@dataclass(slots=True)
class TabResult:
    """One explored tab of a page (§35)."""

    tab_name: str
    url: str
    text: str = ""
    tables: list[TableData] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    documents: list[DocumentLink] = field(default_factory=list)
    screenshot_path: str | None = None
    changed_content: bool = False
    status: str = BrowserStatus.OK.value
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "tab_name": self.tab_name,
            "url": self.url,
            "text": self.text,
            "tables": [t.as_dict() for t in self.tables],
            "links": self.links,
            "documents": [d.as_dict() for d in self.documents],
            "screenshot_path": self.screenshot_path,
            "changed_content": self.changed_content,
            "status": self.status,
            "error": self.error,
        }


@dataclass(slots=True)
class PageSnapshot:
    """RawBrowserSnapshot in memory (§32)."""

    url: str
    page_title: str | None
    fetched_at: datetime
    html_hash: str
    visible_text: str
    status: str = BrowserStatus.OK.value
    screenshot_path: str | None = None
    browser_version: str | None = None
    extractor_version: str = EXTRACTOR_VERSION
    browser_session_id: str | None = None
    html: str | None = None
    language: str | None = None
    http_status: int | None = None
    duration_ms: float | None = None
    from_cache: bool = False

    def source_ref(self, section: str | None = None) -> SourceRef:
        return SourceRef(
            page_url=self.url,
            page_title=self.page_title,
            section=section,
            fetched_at=self.fetched_at,
            browser_session_id=self.browser_session_id,
        )

    def as_dict(self, *, include_html: bool = False) -> dict:
        payload = {
            "url": self.url,
            "page_title": self.page_title,
            "fetched_at": self.fetched_at.isoformat(),
            "html_hash": self.html_hash,
            "visible_text": self.visible_text,
            "screenshot_path": self.screenshot_path,
            "browser_version": self.browser_version,
            "extractor_version": self.extractor_version,
            "browser_session_id": self.browser_session_id,
            "language": self.language,
            "http_status": self.http_status,
            "duration_ms": self.duration_ms,
            "from_cache": self.from_cache,
            "status": self.status,
        }
        if include_html:
            payload["html"] = self.html
        return payload


@dataclass(slots=True)
class NavigationEvent:
    """One line of the navigation log (§40). Never contains credentials."""

    session_id: str
    action_number: int
    action: str
    target: str | None
    url_before: str | None
    url_after: str | None
    status: str
    duration_ms: float
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "action_number": self.action_number,
            "action": self.action,
            "target": self.target,
            "url_before": self.url_before,
            "url_after": self.url_after,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class ActionResult:
    """Return value of every ``browser.*`` command (§39)."""

    action: str
    status: str
    value: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    url: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == BrowserStatus.OK.value

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "status": self.status,
            "value": self.value,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 1),
            "url": self.url,
        }
