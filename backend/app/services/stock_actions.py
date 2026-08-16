"""Strict ingestion of verified stock actions from public KASE publications."""

from __future__ import annotations

from datetime import date, datetime, timezone
from io import BytesIO
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.instrument import Instrument
from app.models.stock import CorporateAction, Dividend, Stock
from app.services.incremental import IncrementalStateService

_AMOUNT_PATTERNS = (
    re.compile(r"(?:дивиденд\w*[^\n]{0,100}?на\s+одну\s+\w*\s*акци\w*|размер\s+дивиденда)[^\d]{0,30}([\d\s]+(?:[,.]\d+)?)\s*(?:тенге|KZT)", re.I),
    re.compile(r"(?:dividend\s+per\s+(?:common|preferred)?\s*share)[^\d]{0,30}([\d\s]+(?:[,.]\d+)?)\s*(?:tenge|KZT)", re.I),
    re.compile(r"(?:KZT|tenge)\s*([\d\s]+(?:[,.]\d+)?)[^\n]{0,40}(?:per\s+share)", re.I),
    re.compile(r"дивиденд\w*[^\n]{0,200}?в\s+размере\s+([\d\s]+(?:[,.]\d+)?)\s*тенге[^\n]{0,80}?на\s+одну\s+\w*\s*акци", re.I),
)
_DATE_RE = re.compile(r"(?<!\d)(\d{2})[./-](\d{2})[./-](\d{4})(?!\d)")


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        match = _DATE_RE.search(str(value))
        if not match:
            return None
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def parse_public_kase_action(item: dict[str, Any]) -> dict[str, Any] | None:
    """Parse only explicit facts; a title with no amount never becomes a dividend."""
    url = str(item.get("url") or "")
    if urlparse(url).hostname not in {"kase.kz", "www.kase.kz"}:
        return None
    title = " ".join(str(item.get("title") or "").split())
    body = " ".join(str(item.get("content") or item.get("body") or item.get("text") or "").split())
    text = f"{title}\n{body}"
    lowered = text.casefold()
    action_type = None
    keyword_map = {
        "dividend": ("дивиденд", "dividend"),
        "split": ("дроблен", "split of shares", "stock split"),
        "reverse_split": ("консолидац", "reverse split"),
        "buyback": ("выкуп акци", "repurchase of shares", "share buyback"),
        "share_issuance": ("размещени акци", "placement of shares", "share issuance"),
        "listing_change": ("делистинг", "исключени из официального списка", "delisting", "listing change"),
        "shareholder_meeting": ("собрани", "general meeting of shareholders"),
    }
    for kind, words in keyword_map.items():
        if any(word in lowered for word in words):
            action_type = kind
            break
    if action_type is None:
        return None
    if any(x in lowered for x in ("отмен", "cancelled", "canceled")):
        status = "cancelled"
    elif any(x in lowered for x in ("завершил выплат", "завершени выплат", "фактической выплат", "completed payment", "paid dividends")):
        status = "paid"
    elif any(x in lowered for x in ("предстоящ", "принял решени", "приняли решени", "решени о выплат", "announced", "decision to pay")):
        status = "announced"
    else:
        status = "unknown"
    result: dict[str, Any] = {
        "action_type": action_type, "status": status, "title": title,
        "event_date": _date(item.get("event_date")), "source_url": url,
        "source_timestamp": _datetime(item.get("publication_date") or item.get("published_at")),
    }
    if action_type == "dividend":
        amount = _number(item.get("dividend_per_share"))
        if amount is None:
            for pattern in _AMOUNT_PATTERNS:
                match = pattern.search(text)
                if match:
                    amount = _number(match.group(1))
                    break
        if amount is None:
            return None
        result.update(
            dividend_per_share=amount,
            currency=str(item.get("currency") or "KZT").upper(),
            ex_date=_date(item.get("ex_date")),
            record_date=_date(item.get("record_date")),
            payment_date=_date(item.get("payment_date")),
        )
    return result


class StockActionIngestionService:
    def __init__(self, session: Session):
        self.session = session
        self.states = IncrementalStateService(session)

    def ingest(self, *, ticker: str | None, items: list[dict[str, Any]]) -> dict[str, int]:
        if not ticker:
            return {}
        stock = self.session.execute(
            select(Stock).join(Stock.instrument).options(joinedload(Stock.instrument)).where(
                or_(func.upper(Instrument.ticker) == ticker.upper(), func.upper(Instrument.isin) == ticker.upper())
            ).limit(1)
        ).unique().scalar_one_or_none()
        if stock is None:
            return {}
        created_dividends = created_actions = 0
        now = datetime.now(timezone.utc)
        for item in items:
            parsed = parse_public_kase_action(item)
            if parsed is None:
                continue
            matching_actions = list(self.session.execute(select(CorporateAction).where(
                CorporateAction.stock_id == stock.id,
                CorporateAction.action_type == parsed["action_type"],
                CorporateAction.source_url == parsed["source_url"],
            ).order_by(CorporateAction.id)).scalars())
            existing_action = matching_actions[0] if matching_actions else None
            for duplicate in matching_actions[1:]:
                self.session.delete(duplicate)
            if existing_action is None:
                existing_action = CorporateAction(stock_id=stock.id, action_type=parsed["action_type"], title=parsed["title"])
                self.session.add(existing_action)
                created_actions += 1
            existing_action.status = parsed["status"]
            existing_action.event_date = parsed["event_date"]
            existing_action.details = {k: _json_value(v) for k, v in parsed.items() if k not in {"title", "source_url", "source_timestamp"}}
            existing_action.source = "kase_public_website"
            existing_action.source_url = parsed["source_url"]
            existing_action.source_timestamp = parsed["source_timestamp"]
            existing_action.fetched_at = now
            if parsed["action_type"] == "dividend":
                matching_dividends = list(self.session.execute(select(Dividend).where(
                    Dividend.stock_id == stock.id,
                    Dividend.source_url == parsed["source_url"],
                ).order_by(Dividend.id)).scalars())
                existing_dividend = matching_dividends[0] if matching_dividends else None
                for duplicate in matching_dividends[1:]:
                    self.session.delete(duplicate)
                if existing_dividend is None:
                    existing_dividend = Dividend(stock_id=stock.id, dividend_per_share=parsed["dividend_per_share"])
                    self.session.add(existing_dividend)
                    created_dividends += 1
                for field in ("dividend_per_share", "currency", "status", "ex_date", "record_date", "payment_date"):
                    setattr(existing_dividend, field, parsed[field])
                existing_dividend.source = "kase_public_website"
                existing_dividend.source_url = parsed["source_url"]
                existing_dividend.source_timestamp = parsed["source_timestamp"]
                existing_dividend.fetched_at = now
        self.session.flush()
        actions = [{"type": row.action_type, "status": row.status, "date": row.event_date, "url": row.source_url} for row in stock.corporate_actions]
        dividends = [{"amount": row.dividend_per_share, "status": row.status, "record_date": row.record_date, "url": row.source_url} for row in stock.dividends]
        self.states.process(entity_type="stock", entity_id=str(stock.id), ticker=stock.instrument.ticker, isin=stock.instrument.isin,
                            section="corporate_actions", payload=actions, source_url=stock.instrument.kase_url or "https://kase.kz/")
        self.states.process(entity_type="stock", entity_id=str(stock.id), ticker=stock.instrument.ticker, isin=stock.instrument.isin,
                            section="dividends", payload=dividends, source_url=stock.instrument.kase_url or "https://kase.kz/")
        return {"dividends_created": created_dividends, "corporate_actions_created": created_actions}


class KaseStockDocumentActionCollector:
    """Reads newly published official issuer PDFs; unchanged lists are skipped."""

    DOCUMENTS_URL = "https://kase.kz/api/companies/documents/"

    def __init__(self, session: Session, client):
        self.session = session
        self.client = client
        self.states = IncrementalStateService(session)

    async def collect(self, stocks_by_issuer: dict[str, list[Stock]]) -> dict[str, int]:
        stats = {"action_document_lists_checked": 0, "action_documents_parsed": 0,
                 "dividends_created": 0, "corporate_actions_created": 0}
        cutoff_year = datetime.now(timezone.utc).year - 2
        keywords = ("дивиденд", "дроблен", "консолидац", "выкуп акци", "размещени акци", "делистинг", "собрани")
        for issuer_code, stocks in stocks_by_issuer.items():
            try:
                response = await self.client.get(self.DOCUMENTS_URL, params={"org_code": issuer_code, "language": "ru"})
            except Exception:
                continue
            if response.status_code != 200:
                continue
            payload = response.json()
            if not isinstance(payload, list):
                continue
            documents = [row for row in payload if isinstance(row, dict) and row.get("category_id") == 7
                         and str(row.get("org_code") or "").upper() == issuer_code.upper()
                         and int(row.get("year") or 0) >= cutoff_year and row.get("link")
                         and any(word in str(row.get("name") or "").casefold() for word in keywords)]
            documents.sort(key=lambda row: str(row.get("date0") or ""), reverse=True)
            normalized = [{"name": row.get("name"), "link": row.get("link"), "date": row.get("date0"), "parser": "stock-actions-v3"} for row in documents]
            changed_stocks = []
            for stock in stocks:
                outcome = self.states.process(entity_type="stock", entity_id=str(stock.id), ticker=stock.instrument.ticker,
                    isin=stock.instrument.isin, section="documents", payload=normalized,
                    source_url=f"{self.DOCUMENTS_URL}?org_code={issuer_code}&language=ru", parser_version="stock-actions-v3")
                if outcome.status != "unchanged":
                    changed_stocks.append(stock)
            stats["action_document_lists_checked"] += 1
            if not changed_stocks:
                continue
            parsed_items = []
            dividend_documents = [row for row in documents if "дивиденд" in str(row.get("name") or "").casefold()][:5]
            for row in documents:
                item = {"title": row.get("name"), "url": f"https://kase.kz{row['link']}", "publication_date": row.get("date0")}
                if row in dividend_documents:
                    try:
                        pdf_response = await self.client.get(item["url"])
                        pdf_response.raise_for_status()
                        from pypdf import PdfReader
                        reader = PdfReader(BytesIO(pdf_response.content))
                        item["content"] = "\n".join(page.extract_text() or "" for page in reader.pages)
                        stats["action_documents_parsed"] += 1
                    except Exception:
                        # Scanned or malformed documents stay unparsed; no
                        # amount or date is substituted.
                        pass
                parsed_items.append(item)
            for stock in changed_stocks:
                result = StockActionIngestionService(self.session).ingest(ticker=stock.instrument.ticker, items=parsed_items)
                stats["dividends_created"] += result.get("dividends_created", 0)
                stats["corporate_actions_created"] += result.get("corporate_actions_created", 0)
        return stats


__all__ = ["KaseStockDocumentActionCollector", "StockActionIngestionService", "parse_public_kase_action"]
