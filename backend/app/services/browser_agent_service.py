"""Orchestration between the browser agent and the database.

This is where §38's flow is enforced end to end:

    plan -> browser executes -> extractor returns candidates ->
    validator checks -> repository writes

Nothing skips a step. In particular the agent's output is never written to a
bond row directly: it goes through ``DataValidator`` first, and only fields
that survive validation are persisted, each with the page URL and timestamp it
came from.

Two other responsibilities live here:

* **no refresh storms** (§50) - a per-ticker in-flight registry means twenty
  clicks on "Проверить на KASE" produce one browser visit, not twenty;
* **a human-readable status** (§49) - the caller gets "Проверено на KASE:
  21:14", never "Playwright clicked div.tab.standard".
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.browser.agent import BondPageResult, KaseBrowserAgent, KaseBrowsingContext
from app.browser.pacing import RuntimeBudget
from app.browser.session import BrowserUnavailableError, browser_service
from app.browser.types import BrowserStatus
from app.core.config import settings
from app.core.enums import DataMode
from app.core.errors import NotFoundError, UpstreamError
from app.core.logging import get_logger
from app.repositories.bonds import BondRepository
from app.repositories.browser import BrowserSnapshotRepository, NavigationLogRepository
from app.repositories.issuers import IssuerRepository
from app.repositories.sources import DataSourceRepository

logger = get_logger(__name__)

#: Bond columns the browser agent is allowed to write, and the validated field
#: they come from. Anything not listed here is stored as provenance only.
WRITABLE_FIELDS = {
    "name": "name",
    "isin": "isin",
    "currency": "currency",
    "nominal": "nominal",
    "issue_date": "issue_date",
    "maturity_date": "maturity_date",
    "coupon_rate": "coupon_rate",
    "coupon_type": "coupon_type",
    "next_coupon_date": "next_coupon_date",
    "day_count": "day_count",
    "issue_size": "issue_size",
    # `outstanding_count` is deliberately absent: the page publishes a number
    # of securities, and `Bond.outstanding_amount` is money. Deriving one from
    # the other is the calculation engine's job, not a browser's.
    "market_segment": "market_segment",
}

MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def human_timestamp(moment: datetime) -> str:
    """"12 августа 2026, 21:14" - what the user should actually be shown."""
    local = moment.astimezone()
    return f"{local.day} {MONTHS_RU[local.month - 1]} {local.year}, {local:%H:%M}"


class RefreshRegistry:
    """One browser visit per ticker at a time, process-wide (§50)."""

    def __init__(self) -> None:
        self._in_flight: dict[str, asyncio.Task] = {}

    async def run(self, key: str, factory):
        existing = self._in_flight.get(key)
        if existing is not None and not existing.done():
            logger.info("joining in-flight KASE refresh for %s", key)
            return await asyncio.shield(existing)
        task = asyncio.create_task(factory())
        self._in_flight[key] = task
        try:
            return await task
        finally:
            if self._in_flight.get(key) is task:
                self._in_flight.pop(key, None)

    @property
    def active(self) -> list[str]:
        return [k for k, t in self._in_flight.items() if not t.done()]


refresh_registry = RefreshRegistry()


class BrowserAgentService:
    def __init__(self, session: Session):
        self.session = session
        self.bonds = BondRepository(session)
        self.issuers = IssuerRepository(session)
        self.snapshots = BrowserSnapshotRepository(session)
        self.nav_log = NavigationLogRepository(session)
        self.sources = DataSourceRepository(session)

    # -- public entry points -----------------------------------------------

    async def verify_bond(
        self,
        identifier: str,
        *,
        sections: list[str] | None = None,
        max_tabs: int = 4,
        with_visual: bool = False,
        persist: bool = True,
    ) -> dict:
        """"Проверить на KASE" for one instrument (§50).

        Looks the bond up locally first (§27/§28: the database is the default
        source), then opens its official page in a browser and reconciles.
        """
        bond = self.bonds.get_by_identifier(identifier)
        ticker = bond.ticker if bond is not None else identifier.strip()
        known_url = bond.kase_url if bond is not None else None

        async def work() -> BondPageResult:
            async with KaseBrowsingContext(label=f"verify:{ticker}") as agent:
                target = known_url if known_url and agent.confirms_domain(known_url) else None
                if target is None and bond is None:
                    # An instrument we have never seen: use the public search
                    # to find its official page rather than guessing a URL.
                    found = await agent.search(ticker, max_results=3)
                    exact = next(
                        (e for e in found if e.ticker.casefold() == ticker.casefold()), None
                    )
                    if exact is not None:
                        target = exact.url
                return await agent.open_bond(
                    ticker,
                    url=target,
                    sections=sections,
                    max_tabs=max_tabs,
                    with_visual=with_visual,
                    use_cache=False,
                    budget=RuntimeBudget(),
                )

        try:
            result = await refresh_registry.run(f"bond:{ticker.casefold()}", work)
        except BrowserUnavailableError as exc:
            raise UpstreamError(
                "Браузерная проверка недоступна на этом сервере.",
                details={"error": str(exc)},
            ) from exc

        payload = self._report(result)
        if persist:
            self._persist(result, bond_ticker=ticker)
            self.session.commit()
        return payload

    async def read_tab(self, identifier: str, section: str) -> dict:
        """"Посмотри вкладку документы" - open one named section and read it."""
        bond = self.bonds.get_by_identifier(identifier)
        ticker = bond.ticker if bond is not None else identifier.strip()

        async def work() -> BondPageResult:
            async with KaseBrowsingContext(label=f"tab:{ticker}") as agent:
                return await agent.open_bond(
                    ticker,
                    url=bond.kase_url if bond is not None else None,
                    sections=[section],
                    max_tabs=1,
                    with_screenshot=False,
                    use_cache=False,
                )

        result = await refresh_registry.run(f"tab:{ticker.casefold()}:{section}", work)
        self._persist(result, bond_ticker=ticker, persist_values=False)
        self.session.commit()
        report = self._report(result)
        report["section"] = section
        report["tab"] = (
            result.tabs_read[0].as_dict() if result.tabs_read else None
        )
        return report

    async def refresh_catalog(self, *, limit: int | None = None) -> dict:
        """Periodic catalogue sweep (§28: the DB is refreshed, not the request)."""

        async def work() -> dict:
            async with KaseBrowsingContext(label="catalog") as agent:
                catalog = await agent.discover_catalog(limit=limit, use_cache=False)
                if catalog.snapshot is not None:
                    self._store_snapshot(
                        catalog.snapshot,
                        kind="catalog",
                        key=None,
                        extracted={"count": len(catalog.entries)},
                        status=catalog.status,
                        error=catalog.error,
                    )
                self._store_nav_log(agent)
                return catalog.as_dict()

        result = await refresh_registry.run("catalog", work)
        self.session.commit()
        return {
            "count": result["count"],
            "truncated": result["truncated"],
            "status": result["status"],
            "source": "KASE",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    async def inspect_url(self, url: str, *, with_visual: bool = False) -> dict:
        """Open an arbitrary public KASE URL and report what is on it."""
        async with KaseBrowsingContext(label="inspect") as agent:
            if not agent.confirms_domain(url):
                raise UpstreamError(
                    "Браузерный агент открывает только официальный сайт KASE.",
                    details={"url": url},
                )
            navigation = await agent._goto(url, min_chars=200)
            content = await agent.extractor.extract(section="page")
            visual = None
            if with_visual and settings.BROWSER_STORE_SCREENSHOTS:
                shot = await agent.session.take_screenshot(name="inspect")
                if shot.ok:
                    analysis = await agent.visual.analyze_file(
                        shot.value,
                        page_context=f"Страница KASE: {url}",
                        task="Опиши структуру страницы и видимые подписи.",
                    )
                    visual = analysis.as_dict()
            self._store_snapshot(
                content.snapshot,
                kind="page",
                key=None,
                extracted={
                    "tables": len(content.tables),
                    "documents": len(content.documents),
                },
                status=navigation.get("status", BrowserStatus.OK.value),
                error=navigation.get("error"),
            )
            self._store_nav_log(agent)
            self.session.commit()
            return {
                "url": content.snapshot.url,
                "page_title": content.snapshot.page_title,
                "status": navigation.get("status"),
                "browser_blocked_by_captcha": bool(
                    navigation.get("browser_blocked_by_captcha")
                ),
                "requires_authentication": bool(navigation.get("requires_authentication")),
                "main_text": content.main_text,
                "tables": [t.as_dict() for t in content.tables],
                "documents": [d.as_dict() for d in content.documents],
                "visual": visual,
                "source": "KASE",
                "checked_at": human_timestamp(content.snapshot.fetched_at),
            }

    # -- persistence -------------------------------------------------------

    def _persist(
        self, result: BondPageResult, *, bond_ticker: str, persist_values: bool = True
    ) -> None:
        if result.snapshot is not None:
            self._store_snapshot(
                result.snapshot,
                kind="bond",
                key=bond_ticker,
                extracted=result.as_dict(include_text=False),
                status=result.status,
                error=result.error,
                blocked=result.browser_blocked_by_captcha,
                requires_auth=result.requires_authentication,
            )
        for tab in result.tabs_read:
            if result.snapshot is None:
                break
            self.snapshots.store(
                {
                    "url": tab.url,
                    "page_title": result.snapshot.page_title,
                    "kind": "tab",
                    "key": bond_ticker,
                    "section": tab.tab_name,
                    "fetched_at": result.snapshot.fetched_at,
                    "html_hash": result.snapshot.html_hash,
                    "visible_text": tab.text[:200_000],
                    "extracted_json": {
                        "tables": [t.as_dict() for t in tab.tables],
                        "documents": [d.as_dict() for d in tab.documents],
                        "changed_content": tab.changed_content,
                    },
                    "screenshot_path": tab.screenshot_path,
                    "browser_version": result.snapshot.browser_version,
                    "extractor_version": result.snapshot.extractor_version,
                    "browser_session_id": result.snapshot.browser_session_id,
                    "language": result.snapshot.language,
                    "status": tab.status,
                    "blocked_by_captcha": False,
                    "requires_authentication": False,
                    "error": tab.error,
                }
            )
        if result.navigation_log:
            self.nav_log.store_many(result.navigation_log)

        if persist_values and result.status == BrowserStatus.OK.value:
            self._apply_values(result, bond_ticker)

        self.sources.get_or_create(
            "kase_browser",
            name="KASE (публичный сайт, браузер)",
            kind="website",
            base_url=settings.KASE_WEBSITE_URL,
            is_authoritative=True,
        )
        if result.status == BrowserStatus.OK.value:
            self.sources.record_success("kase_browser")
        else:
            self.sources.record_failure("kase_browser", result.error or result.status)

    def _apply_values(self, result: BondPageResult, ticker: str) -> None:
        """Write only validated fields, and only onto an instrument we know.

        A bond the database has never heard of is *not* created here: creating
        instruments is the collector's job, with an issuer resolved properly.
        """
        if result.validation is None or not result.validation.accepted:
            return
        bond = self.bonds.get_by_ticker(ticker)
        if bond is None:
            logger.info(
                "browser verified %s but it is not in the database; "
                "values kept in the snapshot only",
                ticker,
            )
            return
        updates: dict = {}
        for column, field_name in WRITABLE_FIELDS.items():
            value = result.validation.value(field_name)
            if value is not None:
                updates[column] = value
        if not updates:
            return
        updates.update(
            {
                "source": "kase_browser",
                "source_identifier": ticker,
                "source_url": result.url,
                "source_timestamp": result.snapshot.fetched_at if result.snapshot else None,
                "fetched_at": result.snapshot.fetched_at if result.snapshot else None,
                "kase_url": result.url,
            }
        )
        self.bonds.upsert(ticker, updates)
        logger.info("browser refresh wrote %d fields for %s", len(updates), ticker)

    def _store_snapshot(
        self,
        snapshot,
        *,
        kind: str,
        key: str | None,
        extracted: dict | None,
        status: str,
        error: str | None = None,
        blocked: bool = False,
        requires_auth: bool = False,
    ) -> None:
        self.snapshots.store(
            {
                "url": snapshot.url,
                "page_title": snapshot.page_title,
                "kind": kind,
                "key": key,
                "section": None,
                "fetched_at": snapshot.fetched_at,
                "html_hash": snapshot.html_hash,
                "visible_text": (snapshot.visible_text or "")[:200_000],
                "extracted_json": extracted,
                "screenshot_path": None,
                "browser_version": snapshot.browser_version,
                "extractor_version": snapshot.extractor_version,
                "browser_session_id": snapshot.browser_session_id,
                "language": snapshot.language,
                "http_status": snapshot.http_status,
                "duration_ms": snapshot.duration_ms,
                "status": status,
                "blocked_by_captcha": blocked,
                "requires_authentication": requires_auth,
                "error": error,
            }
        )

    def _store_nav_log(self, agent: KaseBrowserAgent) -> None:
        events = agent.session.navigation_log_dicts()
        if events:
            self.nav_log.store_many(events)

    # -- presentation (§49) ------------------------------------------------

    def _report(self, result: BondPageResult) -> dict:
        """What the user sees: a source, a time, and any honest caveat."""
        checked_at = (
            result.snapshot.fetched_at if result.snapshot else datetime.now(timezone.utc)
        )
        notice = None
        if result.browser_blocked_by_captcha:
            notice = (
                "KASE запросил проверку «я не робот». Автоматическая проверка "
                "остановлена, показаны последние проверенные данные."
            )
        elif result.requires_authentication:
            notice = "Этот раздел KASE доступен только авторизованным пользователям."
        elif result.status != BrowserStatus.OK.value:
            notice = "Не удалось прочитать страницу на KASE. Показаны сохраненные данные."

        values = result.validation.accepted if result.validation else {}
        warnings = [w.message for w in (result.validation.warnings if result.validation else [])]
        return {
            "ticker": result.ticker,
            "source": "KASE",
            "source_url": result.url,
            "checked_at": checked_at.isoformat(),
            "checked_at_label": f"Проверено на KASE: {human_timestamp(checked_at)}",
            "status": result.status,
            "ok": result.status == BrowserStatus.OK.value,
            "notice": notice,
            "browser_blocked_by_captcha": result.browser_blocked_by_captcha,
            "requires_authentication": result.requires_authentication,
            "data_mode": DataMode.DELAYED.value,
            "identity_confirmed": result.identity_confirmed,
            "tabs_available": [t["tab_name"] for t in result.tabs_available],
            "tabs_read": [
                {
                    "tab_name": t.tab_name,
                    "changed_content": t.changed_content,
                    "tables": len(t.tables),
                    "documents": len(t.documents),
                    "status": t.status,
                }
                for t in result.tabs_read
            ],
            "fields": {name: value.as_dict() for name, value in values.items()},
            "documents": [d.as_dict() for d in result.documents],
            "warnings": warnings,
            "chart": result.chart,
            "visual": result.visual,
        }


def require_browser() -> None:
    if not settings.BROWSER_ENABLED:
        raise NotFoundError("Браузерный агент отключен (BROWSER_ENABLED=false).")


def browser_status() -> dict:
    """Diagnostics for the health endpoint."""
    from app.browser.cache import page_cache

    return {
        **browser_service.status(),
        "cache": page_cache.stats(),
        "refreshes_in_flight": refresh_registry.active,
        "visual_analysis_enabled": settings.BROWSER_VISUAL_ANALYSIS_ENABLED,
        "limits": settings.browser_limits,
    }
