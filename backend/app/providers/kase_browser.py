"""BondDataProvider backed by the browser agent.

This is the seam that makes browsing a first-class data source rather than a
side-channel: the rest of the application keeps talking to ``BondDataProvider``
and neither knows nor cares that behind this one there is a real Chromium
reading kase.kz the way a person does.

It needs no KASE_API_KEY (§2). Everything it returns is public information,
stamped with the page it came from and the moment it was read.

What it deliberately does not do: financial mathematics. Values leave here as
raw + normalized pairs; duration, YTM curves and scores stay in the calculation
engine where they belong (§56).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.browser.agent import KaseBrowserAgent, PATHS
from app.browser.extractors.tables import extract_tables
from app.browser.normalize import (
    normalize_isin,
    normalize_ticker,
    parse_date,
    parse_datetime,
    parse_money,
    parse_number,
    parse_percent,
)
from app.browser.session import BrowserService, BrowserSession, BrowserUnavailableError, browser_service
from app.browser.types import TableData
from app.core.config import settings
from app.core.enums import DataMode
from app.core.errors import UpstreamError
from app.core.logging import get_logger
from app.providers.base import (
    BondDataProvider,
    ProviderBond,
    ProviderDocument,
    ProviderFinancials,
    ProviderIssuer,
    ProviderQuote,
    ProviderRating,
    ProviderStatus,
    ProviderTrade,
    Provenance,
)

logger = get_logger(__name__)

#: KASE prints Almaty local time with no offset. Asia/Almaty is UTC+5 with no
#: DST, so the offset is applied explicitly instead of pretending it is UTC.
KASE_TZ = timezone(timedelta(hours=5))

#: Quote table columns on the market page -> ProviderQuote attributes.
QUOTE_COLUMNS = {
    "лучшая котировка на покупку": "bid",
    "лучшая котировка на продажу": "ask",
    "последн": "last",
    "средневзвеш": "clean_price",
    "объем": "turnover",
    "сделки": "number_of_trades",
    "доходность": "ytm",
}

#: Issuer "Финансовые показатели" rows -> FinancialStatement fields. Rows we
#: have no column for are skipped rather than squeezed into a near-enough one.
FINANCIAL_ROWS = {
    "собственный капитал": "total_equity",
    "совокупные активы": "total_assets",
    "доход от основной деятельности": "revenue",
    "чистая прибыль": "net_profit",
}


def _header_index(headers: list[str], fragment: str) -> int | None:
    for index, header in enumerate(headers):
        if fragment in header.casefold():
            return index
    return None


def _find_table(tables: list[TableData], *required: str) -> TableData | None:
    """First table whose headers contain all of ``required`` (casefolded)."""
    for table in tables:
        folded = " | ".join(table.headers).casefold()
        if all(fragment in folded for fragment in required):
            return table
    return None


class KaseBrowserProvider(BondDataProvider):
    name = "kase_browser"
    data_mode = DataMode.DELAYED.value
    is_mock = False

    def __init__(
        self,
        base_url: str | None = None,
        *,
        service: BrowserService | None = None,
        catalog_limit: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.KASE_WEBSITE_URL).rstrip("/")
        self._service = service or browser_service
        self._session: BrowserSession | None = None
        self._agent: KaseBrowserAgent | None = None
        self.catalog_limit = catalog_limit
        #: Captured for RawKaseData, exactly like the HTTP providers do.
        self.last_raw: list[dict] = []

    # -- session (reused, never one per call - §23) ------------------------

    async def _get_agent(self) -> KaseBrowserAgent:
        if self._agent is not None and self._session is not None:
            return self._agent
        try:
            self._session = await self._service.new_session(label="provider")
        except BrowserUnavailableError as exc:
            raise UpstreamError(
                "Браузерный агент недоступен на этом хосте.",
                details={"error": str(exc)},
            ) from exc
        self._agent = KaseBrowserAgent(self._session, base_url=self.base_url)
        return self._agent

    async def aclose(self) -> None:
        if self._session is not None:
            await self._service.close_session(self._session)
        self._session = None
        self._agent = None

    # -- provenance --------------------------------------------------------

    def _prov(self, identifier: str | None, url: str, ts: datetime) -> Provenance:
        return Provenance(
            source=self.name,
            source_identifier=identifier,
            source_url=url,
            source_timestamp=ts,
            fetched_at=ts,
            data_mode=self.data_mode,
        )

    def _record(self, snapshot, kind: str, key: str | None, payload: dict | None = None) -> None:
        if snapshot is None:
            return
        self.last_raw.append(
            {
                "source": self.name,
                "kind": kind,
                "key": key,
                "url": snapshot.url,
                "http_status": snapshot.http_status,
                "content_type": "text/html",
                "payload_json": payload,
                "payload_text": (snapshot.visible_text or "")[:200_000],
                "payload_hash": snapshot.html_hash,
                "fetched_at": snapshot.fetched_at,
                "duration_ms": snapshot.duration_ms,
                "data_mode": self.data_mode,
            }
        )

    # -- bonds -------------------------------------------------------------

    async def get_bonds(self) -> list[ProviderBond]:
        agent = await self._get_agent()
        catalog = await agent.discover_catalog(limit=self.catalog_limit)
        self._record(catalog.snapshot, "browser_catalog", None, {"count": len(catalog.entries)})
        if catalog.status != "ok":
            raise UpstreamError(
                "Каталог облигаций KASE не удалось прочитать в браузере.",
                details={"status": catalog.status, "error": catalog.error},
            )
        if not catalog.entries:
            raise UpstreamError(
                "Страница каталога KASE открылась, но ни одного инструмента "
                "распознать не удалось.",
                details={"url": catalog.snapshot.url if catalog.snapshot else None},
            )
        ts = catalog.snapshot.fetched_at if catalog.snapshot else datetime.now(timezone.utc)
        return [
            ProviderBond(
                ticker=entry.ticker,
                # The catalogue prints the issuer's display name, not its KASE
                # code. Resolving the code needs the instrument page, so the
                # collector does it - inventing one here would be a fabrication.
                name=entry.raw.get("Компания") or entry.issuer_name or entry.ticker,
                issuer_code="",
                isin=entry.isin,
                currency=entry.currency or "KZT",
                kase_url=entry.url,
                provenance=self._prov(entry.ticker, entry.url, ts),
            )
            for entry in catalog.entries
        ]

    async def get_bond(self, identifier: str) -> ProviderBond | None:
        agent = await self._get_agent()
        ticker = normalize_ticker(identifier) or identifier
        result = await agent.open_bond(ticker, with_screenshot=False, max_tabs=2)
        self._record(
            result.snapshot,
            "browser_bond",
            ticker,
            result.validation.as_dict() if result.validation else None,
        )
        if result.status != "ok" or result.validation is None:
            logger.info("browser bond page failed ticker=%s status=%s", ticker, result.status)
            return None

        values = result.validation
        ts = result.snapshot.fetched_at if result.snapshot else datetime.now(timezone.utc)
        return ProviderBond(
            ticker=values.value("ticker") or ticker,
            name=values.value("name") or ticker,
            issuer_code="",
            isin=values.value("isin"),
            currency=values.value("currency") or "KZT",
            nominal=values.value("nominal"),
            issue_date=values.value("issue_date"),
            maturity_date=values.value("maturity_date"),
            coupon_rate=values.value("coupon_rate"),
            coupon_type=values.value("coupon_type"),
            next_coupon_date=values.value("next_coupon_date"),
            day_count=values.value("day_count") or "ACT/365F",
            issue_size=values.value("issue_size"),
            # NOTE: the page states the *number* of outstanding securities,
            # while `outstanding_amount` is money. Turning one into the other
            # is a calculation, so it is left to the engine rather than
            # smuggled in here as if the site had published it.
            market_segment=values.value("market_segment"),
            kase_url=result.url,
            provenance=self._prov(ticker, result.url or "", ts),
        )

    async def search_bonds(self, query: str) -> list[ProviderBond]:
        agent = await self._get_agent()
        entries = await agent.search(query)
        ts = datetime.now(timezone.utc)
        return [
            ProviderBond(
                ticker=entry.ticker,
                name=entry.issuer_name or entry.ticker,
                issuer_code="",
                isin=entry.isin,
                currency=entry.currency or "KZT",
                kase_url=entry.url,
                provenance=self._prov(entry.ticker, entry.url, ts),
            )
            for entry in entries
        ]

    # -- market ------------------------------------------------------------

    async def get_quotes(self, tickers: list[str] | None = None) -> list[ProviderQuote]:
        """End-of-session figures from the public market page's trading table."""
        agent = await self._get_agent()
        url = agent.url_for("catalog")
        navigation = await agent._goto(url, min_chars=800)
        if not navigation["ok"]:
            raise UpstreamError(
                "Страница рынка KASE недоступна для браузерного агента.",
                details=navigation,
            )
        tables = await extract_tables(agent.session, section="market")
        table = _find_table(tables, "лучшая котировка")
        snapshot = await agent.session.snapshot(section="market")
        self._record(snapshot, "browser_quotes", None, {"tables": len(tables)})
        if table is None:
            logger.info("no quote table found on %s", url)
            return []

        headers = [h.casefold() for h in table.headers]
        code_index = _header_index(table.headers, "код")
        if code_index is None:
            return []
        wanted = {t.upper() for t in tickers} if tickers else None

        quotes: list[ProviderQuote] = []
        for row in table.rows:
            if code_index >= len(row):
                continue
            ticker = normalize_ticker(row[code_index])
            if not ticker or (wanted and ticker.upper() not in wanted):
                continue
            quote = ProviderQuote(
                ticker=ticker,
                timestamp=snapshot.fetched_at,
                provenance=self._prov(ticker, snapshot.url, snapshot.fetched_at),
            )
            for index, cell in enumerate(row):
                if index >= len(headers):
                    break
                header = headers[index]
                for fragment, attribute in QUOTE_COLUMNS.items():
                    if fragment not in header:
                        continue
                    if attribute == "ytm":
                        setattr(quote, attribute, parse_percent(cell))
                    elif attribute == "number_of_trades":
                        value = parse_number(cell)
                        quote.number_of_trades = None if value is None else int(value)
                    elif attribute == "turnover":
                        amount, _ = parse_money(cell)
                        # The column is headed "Объем, млн KZT".
                        quote.turnover = None if amount is None else amount * 1_000_000
                    else:
                        setattr(quote, attribute, parse_number(cell))
                    break
            quotes.append(quote)
        logger.info("browser quotes read: %d rows from %s", len(quotes), url)
        return quotes

    async def get_trades(
        self, ticker: str, *, since: datetime | None = None
    ) -> list[ProviderTrade]:
        """The instrument page's "last trades" table."""
        agent = await self._get_agent()
        url = agent.url_for("bond", ticker=ticker)
        navigation = await agent._goto(url, min_chars=300)
        if not navigation["ok"]:
            return []
        tables = await extract_tables(agent.session, section="trades")
        table = _find_table(tables, "дата сделки")
        snapshot = await agent.session.snapshot(section="trades")
        self._record(snapshot, "browser_trades", ticker, {"tables": len(tables)})
        if table is None:
            return []

        date_index = _header_index(table.headers, "дата сделки")
        time_index = _header_index(table.headers, "время сделки")
        price_index = _header_index(table.headers, "цена, значение")
        qty_index = _header_index(table.headers, "объем, шт")
        amount_index = _header_index(table.headers, "млн kzt")
        if date_index is None or price_index is None:
            return []

        trades: list[ProviderTrade] = []
        for row in table.rows:
            if date_index >= len(row):
                continue
            stamp_text = row[date_index]
            if time_index is not None and time_index < len(row) and row[time_index]:
                stamp_text = f"{row[date_index]} {row[time_index]}"
            timestamp = parse_datetime(stamp_text, assume_tz=KASE_TZ)
            if timestamp is None:
                continue
            if since is not None and timestamp < since:
                continue
            amount = None
            if amount_index is not None and amount_index < len(row):
                raw_amount, _ = parse_money(row[amount_index])
                amount = None if raw_amount is None else raw_amount * 1_000_000
            trades.append(
                ProviderTrade(
                    ticker=ticker,
                    timestamp=timestamp,
                    price=parse_number(row[price_index]) if price_index < len(row) else None,
                    clean_price=parse_number(row[price_index]) if price_index < len(row) else None,
                    quantity=parse_number(row[qty_index]) if qty_index is not None and qty_index < len(row) else None,
                    amount=amount,
                    currency="KZT",
                    provenance=self._prov(ticker, snapshot.url, snapshot.fetched_at),
                )
            )
        return trades

    # -- issuer ------------------------------------------------------------

    async def _open_issuer(self, code: str):
        agent = await self._get_agent()
        url = agent.url_for("issuer", code=code)
        navigation = await agent._goto(url, min_chars=200)
        if not navigation["ok"]:
            return agent, None
        return agent, await agent.extractor.extract(section="issuer")

    async def get_issuer(self, identifier: str) -> ProviderIssuer | None:
        agent, content = await self._open_issuer(identifier)
        if content is None:
            return None
        self._record(content.snapshot, "browser_issuer", identifier, None)
        title = (content.snapshot.page_title or "").split(" - ")[0].strip()
        name = title or content.main_text.splitlines()[0].strip()
        if not name:
            return None
        pairs = content.label_values
        return ProviderIssuer(
            code=identifier.upper(),
            name=name,
            website=pairs.get("Сайт") or pairs.get("Веб-сайт"),
            sector=pairs.get("Основная деятельность"),
            kase_url=content.snapshot.url,
            description=pairs.get("Основная деятельность"),
            provenance=self._prov(
                identifier, content.snapshot.url, content.snapshot.fetched_at
            ),
        )

    async def get_financials(self, issuer_code: str) -> list[ProviderFinancials]:
        """The issuer page's summary indicators table.

        These are KASE's own published aggregates, not a parsed IFRS statement.
        Full statements live in the PDFs surfaced by ``get_documents``, and are
        the document pipeline's job (§17).
        """
        agent, content = await self._open_issuer(issuer_code)
        if content is None:
            return []
        table = _find_table(content.tables, "финансовые показатели")
        self._record(content.snapshot, "browser_financials", issuer_code, None)
        if table is None or not table.headers:
            return []

        # Column 0 is the row label; the remaining headers are period dates.
        periods: list[tuple[int, object]] = []
        for index, header in enumerate(table.headers[1:], start=1):
            period_end = parse_date(header)
            if period_end is not None:
                periods.append((index, period_end))
        if not periods:
            return []

        statements: list[ProviderFinancials] = []
        for index, period_end in periods:
            values: dict[str, float | None] = {}
            for row in table.rows:
                if not row or index >= len(row):
                    continue
                field_name = None
                label = row[0].casefold()
                for fragment, name in FINANCIAL_ROWS.items():
                    if fragment in label:
                        field_name = name
                        break
                if field_name is None:
                    continue
                amount, _currency = parse_money(row[index])
                if amount is not None:
                    values[field_name] = amount
            if not values:
                continue
            statements.append(
                ProviderFinancials(
                    issuer_code=issuer_code.upper(),
                    period_end=period_end,  # type: ignore[arg-type]
                    period_type="Q" if period_end.month != 12 else "FY",  # type: ignore[union-attr]
                    currency="KZT",
                    values=values,
                    provenance=self._prov(
                        issuer_code, content.snapshot.url, content.snapshot.fetched_at
                    ),
                )
            )
        return statements

    async def get_documents(self, issuer_code: str) -> list[ProviderDocument]:
        agent, content = await self._open_issuer(issuer_code)
        if content is None:
            return []
        self._record(content.snapshot, "browser_documents", issuer_code, None)
        return [
            ProviderDocument(
                issuer_code=issuer_code.upper(),
                title=document.name,
                url=document.url,
                kind=document.document_type,
                published_at=parse_date(document.publication_date),
                provenance=self._prov(
                    issuer_code, document.source_page, content.snapshot.fetched_at
                ),
            )
            for document in content.documents
        ]

    async def get_ratings(self, issuer_code: str) -> list[ProviderRating]:
        """KASE publishes ratings as free-text news, not as structured data.

        Parsing an agency and an outlook out of a headline would be a guess, so
        this returns nothing rather than something plausible.
        """
        return []

    # -- health ------------------------------------------------------------

    async def health(self) -> ProviderStatus:
        started = datetime.now(timezone.utc)
        try:
            agent = await self._get_agent()
        except UpstreamError as exc:
            return ProviderStatus(
                name=self.name,
                reachable=False,
                data_mode=self.data_mode,
                checked_at=started,
                detail=f"Браузер не запускается: {exc.message}",
                is_mock=False,
            )
        url = agent.url_for("catalog")
        navigation = await agent._goto(url, min_chars=500)
        latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
        if not navigation["ok"]:
            detail = {
                "blocked_by_captcha": "KASE показал CAPTCHA. Автоматический обход не выполняется.",
                "requires_authentication": "Страница требует авторизации.",
            }.get(navigation.get("status", ""), f"Недоступно: {navigation.get('error')}")
            return ProviderStatus(
                name=self.name,
                reachable=False,
                data_mode=self.data_mode,
                checked_at=started,
                latency_ms=latency,
                detail=detail,
                is_mock=False,
            )
        tables = await extract_tables(agent.session, section="health")
        rows = max((len(t.rows) for t in tables), default=0)
        return ProviderStatus(
            name=self.name,
            reachable=True,
            data_mode=self.data_mode,
            checked_at=started,
            latency_ms=latency,
            detail=f"Браузер открыл {url}; строк в таблицах: {rows}.",
            is_mock=False,
        )


__all__ = ["KaseBrowserProvider", "PATHS"]
