"""Automatic, official KASE share discovery and incremental ingestion."""

from __future__ import annotations

from datetime import date, datetime, timezone
import asyncio
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock, StockFinancialPeriod, StockQuote
from app.providers.kase_public_api import KasePublicApiProvider
from app.services.incremental import IncrementalStateService, content_hash

CATALOG_URL = "https://kase.kz/api/instruments/securities/"
SOURCE_NAME = "kase_public_website"
PARSER_VERSION = "stock-catalog-v1"


def _text(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value or None


def _float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
        return parsed.replace(tzinfo=timezone.utc) if parsed and parsed.tzinfo is None else parsed
    except ValueError:
        return None


class KaseStockCatalogCollector:
    """Discovers shares from KASE; no ticker list is embedded in the product."""

    def __init__(self, session: Session, *, base_url: str = "https://kase.kz", client: httpx.AsyncClient | None = None):
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.client = client

    @staticmethod
    def parse_catalog(payload: Any) -> list[dict]:
        if not isinstance(payload, list):
            return []
        items: list[dict] = []
        for row in payload:
            ticker_data = row.get("ticker") if isinstance(row, dict) else None
            # Historical/delisted rows have no active ticker metadata. KASE Global
            # is deliberately excluded from the local KASE equity universe.
            if not isinstance(ticker_data, dict) or ticker_data.get("finish_date") or row.get("fin_sec_en") != "shares":
                continue
            ticker = _text(row.get("code"))
            isin = _text(ticker_data.get("nin"))
            if not ticker or not isin:
                continue
            subtype = (_text(row.get("subcategory_name_en")) or _text(ticker_data.get("typesec_en")) or "ordinary share").lower()
            preferred = "preferred" in subtype
            source_ts = _datetime(row.get("date0"))
            items.append({
                "ticker": ticker, "isin": isin.upper(), "issuer_code": _text(row.get("org_code")) or ticker,
                "issuer": _text(row.get("org_name_en")) or _text(row.get("org_name_ru")) or ticker,
                "company_name": _text(row.get("org_short_name_en")) or _text(row.get("org_name_en")) or ticker,
                "instrument_type": "preferred_stock" if preferred else "stock", "share_class": "preferred" if preferred else "ordinary",
                "security_type": subtype, "currency": (_text(row.get("currency_type")) or "KZT").upper(),
                "market_segment": _text(row.get("board_en")), "listing_status": _text(ticker_data.get("securities_list_en")) or "official",
                "listing_date": _date(ticker_data.get("open_trade_date") or ticker_data.get("incl_date")),
                "is_active": True, "price": _float(row.get("price")), "close": _float(row.get("close_price")),
                "bid": _float(row.get("best_bid")), "ask": _float(row.get("best_offer")),
                "turnover": _float(row.get("volkzt")), "number_of_trades": int(row["dealcnt"]) if row.get("dealcnt") is not None else None,
                "liquidity_class": int(row["liquid_class"]) if row.get("liquid_class") is not None else None,
                "shares_outstanding": _float(row.get("volume_release_number")),
                "source_timestamp": source_ts,
            })
        return items

    async def fetch(self) -> list[dict]:
        if self.client is not None:
            response = await self.client.get(CATALOG_URL, params={"sec_type": "share"})
        else:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers={"User-Agent": "KASE-Bond-AI/stock-collector"}) as client:
                response = await client.get(CATALOG_URL, params={"sec_type": "share"})
        response.raise_for_status()
        return self.parse_catalog(response.json())

    async def collect(self) -> dict:
        rows = await self.fetch()
        if not rows:
            raise RuntimeError("KASE stock catalog is empty or failed validation")
        now = datetime.now(timezone.utc)
        incremental = IncrementalStateService(self.session)
        stats = {"discovered": len(rows), "created": 0, "updated": 0, "quotes_created": 0, "unchanged_sections": 0}
        seen: set[tuple[str, str]] = set(); stocks_by_issuer: dict[str, list[Stock]] = {}
        for item in rows:
            issuer = self.session.execute(select(Issuer).where(func.upper(Issuer.code) == item["issuer_code"].upper())).scalar_one_or_none()
            if issuer is None:
                issuer = Issuer(code=item["issuer_code"], name=item["issuer"], short_name=item["company_name"], country="KZ", is_active=True,
                                source=SOURCE_NAME, source_url=CATALOG_URL, fetched_at=now)
                self.session.add(issuer); self.session.flush()
            instrument = self.session.execute(select(Instrument).where(Instrument.instrument_type == item["instrument_type"], func.upper(Instrument.ticker) == item["ticker"].upper())).scalar_one_or_none()
            created = instrument is None
            kase_url = f"{self.base_url}/en/investors/instruments/shares/{item['ticker']}"
            if instrument is None:
                instrument = Instrument(ticker=item["ticker"], isin=item["isin"], issuer_id=issuer.id, instrument_type=item["instrument_type"])
                self.session.add(instrument)
            for field in ("isin", "security_type", "currency", "market_segment", "listing_status", "is_active"):
                setattr(instrument, field, item[field])
            instrument.kase_url = kase_url; instrument.source = SOURCE_NAME; instrument.source_url = CATALOG_URL
            instrument.source_timestamp = item["source_timestamp"]; instrument.fetched_at = now
            self.session.flush()
            stock = self.session.execute(select(Stock).where(Stock.instrument_id == instrument.id)).scalar_one_or_none()
            if stock is None:
                stock = Stock(instrument_id=instrument.id, share_class=item["share_class"], listing_date=item["listing_date"], lot_size=1)
                self.session.add(stock); self.session.flush()
            stock.liquidity_class = item["liquidity_class"]; stock.last_checked_at = now
            if item["shares_outstanding"] is not None:
                stock.shares_outstanding = item["shares_outstanding"]
            stocks_by_issuer.setdefault(item["issuer_code"], []).append(stock)
            profile = {key: item[key] for key in ("ticker", "isin", "issuer", "company_name", "instrument_type", "share_class", "security_type", "currency", "market_segment", "listing_status", "listing_date", "is_active")}
            profile_result = incremental.process(entity_type="stock", entity_id=str(stock.id), section="profile", payload=profile, source_url=kase_url,
                                                 ticker=item["ticker"], isin=item["isin"], source_timestamp=item["source_timestamp"], parser_version=PARSER_VERSION)
            if profile_result.status == "unchanged": stats["unchanged_sections"] += 1
            else: stock.last_changed_at = now
            quote_payload = {key: item[key] for key in ("bid", "ask", "price", "close", "turnover", "number_of_trades", "liquidity_class")}
            quote_result = incremental.process(entity_type="stock", entity_id=str(stock.id), section="quote", payload=quote_payload, source_url=CATALOG_URL,
                                               ticker=item["ticker"], isin=item["isin"], source_timestamp=item["source_timestamp"], parser_version=PARSER_VERSION)
            incremental.process(entity_type="stock", entity_id=str(stock.id), section="order_book", payload={"bid": item["bid"], "ask": item["ask"]}, source_url=CATALOG_URL,
                                ticker=item["ticker"], isin=item["isin"], source_timestamp=item["source_timestamp"], parser_version=PARSER_VERSION)
            if quote_result.status == "unchanged":
                stats["unchanged_sections"] += 1
            else:
                digest = content_hash(quote_payload)
                quote = StockQuote(stock_id=stock.id, timestamp=item["source_timestamp"] or now, bid=item["bid"], ask=item["ask"], last=item["price"], close=item["close"],
                                   turnover=item["turnover"], number_of_trades=item["number_of_trades"], data_mode="delayed", content_hash=digest,
                                   source=SOURCE_NAME, source_url=CATALOG_URL, source_timestamp=item["source_timestamp"], fetched_at=now)
                self.session.add(quote); stats["quotes_created"] += 1
            stats["created" if created else "updated"] += 1
            seen.add((item["instrument_type"], item["ticker"].upper()))
        # Mark only previously known stock instruments absent from a validated
        # full catalog inactive; bond rows and their contracts are untouched.
        for instrument in self.session.execute(select(Instrument).where(Instrument.instrument_type.in_(("stock", "preferred_stock")))).scalars():
            if (instrument.instrument_type, instrument.ticker.upper()) not in seen:
                instrument.is_active = False
        financial_stats = await self._collect_financials(stocks_by_issuer, incremental, now)
        stats.update(financial_stats)
        self.session.commit()
        return stats

    async def _collect_financials(self, stocks_by_issuer: dict[str, list[Stock]], incremental: IncrementalStateService, now: datetime) -> dict:
        """KASE fin-data is issuer-level; fan it out to each listed share class."""
        provider = KasePublicApiProvider(self.base_url, language="en", max_concurrency=4)
        issuer_codes = sorted(stocks_by_issuer)
        results = await asyncio.gather(*(provider.get_financials(code) for code in issuer_codes), return_exceptions=True)
        periods_saved = 0; issuers_with_data = 0
        for code, statements in zip(issuer_codes, results, strict=True):
            if isinstance(statements, Exception) or not statements:
                continue
            issuers_with_data += 1
            # The public feed can repeat one reporting date (for example an
            # amended row). Keep the most complete official row; never create
            # duplicate period records.
            unique_statements = {}
            for statement in statements:
                key = (statement.period_end, statement.period_type)
                known = unique_statements.get(key)
                completeness = sum(value is not None for value in statement.values.values())
                if known is None or completeness > sum(value is not None for value in known.values.values()):
                    unique_statements[key] = statement
            for stock in stocks_by_issuer[code]:
                normalized = []
                for statement in unique_statements.values():
                    values = statement.values
                    record = {"period_end": statement.period_end, "period_type": statement.period_type, "currency": statement.currency,
                              "revenue": values.get("revenue"), "net_income": values.get("net_profit"), "total_assets": values.get("total_assets"),
                              "total_equity": values.get("total_equity"), "is_audited": statement.is_audited}
                    normalized.append(record)
                    existing = self.session.execute(select(StockFinancialPeriod).where(StockFinancialPeriod.stock_id == stock.id,
                        StockFinancialPeriod.period_end == statement.period_end, StockFinancialPeriod.period_type == statement.period_type)).scalar_one_or_none()
                    if existing is None:
                        existing = StockFinancialPeriod(stock_id=stock.id, period_end=statement.period_end, period_type=statement.period_type)
                        self.session.add(existing); periods_saved += 1
                    for key, value in record.items():
                        if key not in {"period_end", "period_type"} and value is not None:
                            setattr(existing, key, value)
                    existing.source = SOURCE_NAME; existing.source_url = statement.provenance.source_url
                    existing.source_timestamp = statement.provenance.source_timestamp; existing.fetched_at = now
                incremental.process(entity_type="stock", entity_id=str(stock.id), section="financials", payload={"financials": normalized},
                                    source_url=f"{self.base_url}/api/companies/fin-data/{code}/", ticker=stock.instrument.ticker,
                                    isin=stock.instrument.isin, parser_version=PARSER_VERSION, validated_missing=True)
        return {"issuers_with_financials": issuers_with_data, "financial_periods_created": periods_saved}


__all__ = ["KaseStockCatalogCollector", "CATALOG_URL"]
