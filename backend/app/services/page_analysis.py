"""Turn a browsing session into an analysis.

The agent opens a bond's page on kase.kz the way a person does - it walks the
tabs, flips the clean/dirty/yield toggles, reads the tables - and this module
turns what it saw into findings.

The division of labour is the same one the rest of the product uses: **the
observations here are computed, the narrative is not**. Every number in the
result was extracted from the page or already stored in the database; the
language model receives that finished payload and only puts it into words. If
the model is unavailable the findings are unchanged and a deterministic
summary is served instead, because the analysis is the findings, not the prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.models.bond import Bond

logger = get_logger(__name__)

#: Fields worth cross-checking between the page and our stored record. A
#: mismatch here means one of the two is wrong, which the user deserves to know.
COMPARABLE_FIELDS: dict[str, str] = {
    "isin": "isin",
    "coupon_rate": "coupon_rate",
    "nominal": "nominal",
    "maturity_date": "maturity_date",
    "next_coupon_date": "next_coupon_date",
    "currency": "currency",
    "issue_date": "issue_date",
}

#: Relative tolerance for numeric comparison; below this it is rounding.
NUMERIC_TOLERANCE = 0.005


@dataclass(slots=True)
class Finding:
    """One thing worth telling the user, with where it came from."""

    kind: str  # observation | mismatch | warning | limitation
    code: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }


def _as_comparable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date()
    return value


def _values_differ(page_value: Any, stored_value: Any) -> bool:
    page_value = _as_comparable(page_value)
    stored_value = _as_comparable(stored_value)
    if page_value is None or stored_value is None:
        return False
    if isinstance(page_value, (int, float)) and isinstance(stored_value, (int, float)):
        if stored_value == 0:
            return abs(page_value) > NUMERIC_TOLERANCE
        return abs(page_value - stored_value) / abs(stored_value) > NUMERIC_TOLERANCE
    if isinstance(page_value, date) and isinstance(stored_value, date):
        return page_value != stored_value
    return str(page_value).strip().casefold() != str(stored_value).strip().casefold()


def analyze_page(result: dict, bond: Bond | None = None) -> dict:
    """Findings from one browsing session, computed - never generated.

    ``result`` is ``BondPageResult.as_dict()``. ``bond`` is our stored record,
    when we have one, so the page can be checked against it.
    """
    findings: list[Finding] = []
    values = result.get("values") or {}

    # -- did we actually see the right page? ------------------------------
    if result.get("browser_blocked_by_captcha"):
        findings.append(Finding(
            "limitation", "captcha",
            "Страница потребовала прохождения CAPTCHA — данные не читались.",
        ))
    if result.get("requires_authentication"):
        findings.append(Finding(
            "limitation", "auth_required",
            "Раздел доступен только авторизованным пользователям.",
        ))
    if not result.get("identity_confirmed"):
        findings.append(Finding(
            "warning", "identity_unconfirmed",
            "Страница не подтвердила, что относится к запрошенному выпуску.",
        ))

    # -- what the agent walked --------------------------------------------
    tabs_read = result.get("tabs_read") or []
    views_read = result.get("views_read") or []
    available = result.get("tabs_available") or []
    sections_seen = [t.get("tab_name") for t in tabs_read]
    if sections_seen:
        findings.append(Finding(
            "observation", "tabs_walked",
            "Открыты вкладки: " + ", ".join(str(s) for s in sections_seen) + ".",
            {"tabs": sections_seen, "available": len(available)},
        ))

    unread = [
        tab.get("tab_name")
        for tab in available
        if tab.get("section") and tab.get("tab_name") not in sections_seen
    ]
    if unread:
        findings.append(Finding(
            "limitation", "tabs_not_read",
            "Не открыты вкладки: " + ", ".join(str(u) for u in unread) + ".",
            {"tabs": unread},
        ))

    # -- the price views a user can switch between ------------------------
    views = _summarize_views(views_read)
    if views:
        findings.append(Finding(
            "observation", "price_views",
            _describe_views(views),
            {"views": views},
        ))

    # -- page vs our database ---------------------------------------------
    mismatches: list[dict] = []
    if bond is not None:
        for field_name, attribute in COMPARABLE_FIELDS.items():
            entry = values.get(field_name)
            if not entry:
                continue
            page_value = entry.get("normalized_value")
            stored_value = getattr(bond, attribute, None)
            if _values_differ(page_value, stored_value):
                mismatches.append({
                    "field": field_name,
                    "on_page": str(page_value),
                    "in_database": str(stored_value),
                    "page_confidence": entry.get("confidence"),
                })
        for mismatch in mismatches:
            findings.append(Finding(
                "mismatch", f"mismatch_{mismatch['field']}",
                f"{mismatch['field']}: на странице {mismatch['on_page']}, "
                f"в базе {mismatch['in_database']}.",
                mismatch,
            ))
        if not mismatches and values:
            findings.append(Finding(
                "observation", "matches_database",
                "Сверенные поля на странице совпадают с сохраненными данными.",
            ))

    # -- conflicts the extractor itself found -----------------------------
    validation = result.get("validation") or {}
    conflicts = validation.get("conflicts") or []
    for conflict in conflicts:
        findings.append(Finding(
            "warning", "extraction_conflict",
            f"Одно поле прочиталось по-разному в разных местах страницы: {conflict}.",
            {"conflict": conflict},
        ))

    documents = result.get("documents") or []
    if documents:
        findings.append(Finding(
            "observation", "documents",
            f"Найдено документов эмитента: {len(documents)}.",
            {"count": len(documents),
             "titles": [d.get("document_name") for d in documents[:5]]},
        ))

    chart = result.get("chart") or {}
    if chart and not chart.get("precise_values_available"):
        findings.append(Finding(
            "limitation", "chart_not_machine_readable",
            "График на странице не отдает точных значений — из него ничего не бралось.",
        ))

    return {
        "ticker": result.get("ticker"),
        "url": result.get("url"),
        "read_at": (result.get("snapshot") or {}).get("fetched_at")
        or datetime.now(timezone.utc).isoformat(),
        "identity_confirmed": bool(result.get("identity_confirmed")),
        "tabs_available": len(available),
        "tabs_read": [t.get("tab_name") for t in tabs_read],
        "views_read": [t.get("view") for t in views_read if t.get("view")],
        "fields_extracted": len(values),
        "facts": _facts(values),
        "price_views": views,
        "mismatches": mismatches,
        "documents": [
            {"name": d.get("document_name"), "url": d.get("document_url")}
            for d in documents[:10]
        ],
        "findings": [f.as_dict() for f in findings],
    }


def _facts(values: dict) -> dict:
    """The validated field values, flattened for display and for the prompt."""
    return {
        name: {
            "value": entry.get("normalized_value"),
            "label": entry.get("label"),
            "confidence": entry.get("confidence"),
            "method": entry.get("method"),
        }
        for name, entry in values.items()
    }


def _summarize_views(views_read: list[dict]) -> dict:
    """Best bid/ask per price view, taken from the first row of each table.

    The three views render the *same* table with different numbers, which is
    why they are summarised side by side: it is the clearest way to show that
    a clean price, a dirty price and a yield are three descriptions of one
    quote, not three different quotes.
    """
    summary: dict[str, dict] = {}
    for entry in views_read:
        view = entry.get("view")
        if not view:
            continue
        for table in entry.get("tables") or []:
            headers = [str(h).strip().casefold() for h in (table.get("headers") or [])]
            if "bid" not in headers or "offer" not in headers:
                continue
            rows = table.get("rows") or []
            for row in rows:
                bid = _cell(row, headers, "bid")
                offer = _cell(row, headers, "offer")
                if bid is None and offer is None:
                    continue
                summary[view] = {
                    "period": row[0] if row else None,
                    "bid": bid,
                    "offer": offer,
                }
                break
            if view in summary:
                break
    return summary


def _cell(row: list, headers: list[str], name: str) -> str | None:
    try:
        index = headers.index(name)
    except ValueError:
        return None
    if index >= len(row):
        return None
    value = str(row[index]).strip()
    return None if value in {"", "-", "–", "—"} else value


def _describe_views(views: dict) -> str:
    parts = []
    labels = {
        "clean_price": "чистая цена",
        "dirty_price": "грязная цена",
        "yield": "доходность",
    }
    for view, data in views.items():
        bid = data.get("bid") or "нет"
        offer = data.get("offer") or "нет"
        parts.append(f"{labels.get(view, view)}: спрос {bid}, предложение {offer}")
    return "Переключены представления котировок — " + "; ".join(parts) + "."


def deterministic_summary(analysis: dict) -> str:
    """A readable summary with no language model involved.

    This is what the endpoint serves when the LLM is disabled or unreachable,
    and it is also the fallback the model's output is compared against.
    """
    lines: list[str] = []
    ticker = analysis.get("ticker")
    tabs = analysis.get("tabs_read") or []
    views = analysis.get("views_read") or []

    opened = f"Открыта страница {ticker} на KASE"
    if tabs:
        opened += f", просмотрены вкладки: {', '.join(str(t) for t in tabs)}"
    if views:
        opened += f"; переключены представления: {', '.join(str(v) for v in views)}"
    lines.append(opened + ".")

    lines.append(f"Со страницы прочитано полей: {analysis.get('fields_extracted', 0)}.")

    mismatches = analysis.get("mismatches") or []
    if mismatches:
        detail = "; ".join(
            f"{m['field']} — на странице {m['on_page']}, в базе {m['in_database']}"
            for m in mismatches
        )
        lines.append(f"Расхождения с сохраненными данными: {detail}.")
    elif analysis.get("fields_extracted"):
        lines.append("Расхождений со страницей не обнаружено.")

    limitations = [
        f["message"] for f in analysis.get("findings", []) if f["kind"] == "limitation"
    ]
    if limitations:
        lines.append(" ".join(limitations))

    documents = analysis.get("documents") or []
    if documents:
        lines.append(f"Доступно документов эмитента: {len(documents)}.")

    return " ".join(lines)
