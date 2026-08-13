"""Official Kazakhstan inflation, from the primary source.

The publisher of record is the Bureau of National Statistics of the Agency for
Strategic Planning and Reforms (БНС АСПР РК) at ``stat.gov.kz``. It releases a
monthly bulletin, "Инфляция в Республике Казахстан", carrying the
year-on-year CPI.

There is no JSON API. The bulletins are HTML pages behind opaque numeric ids
and XLSX attachments, so this collector reads the publications index, follows
the newest inflation bulletin and extracts the headline figure. Two
consequences are accepted rather than hidden:

* The parser is tied to stat.gov.kz's markup and will break when the site is
  restyled. It fails loudly and leaves the previous stored reading untouched -
  a stale but real number beats a fresh invented one.
* A manual override always wins, so an operator can set the rate the moment a
  release lands or the parser breaks.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.macro import InflationData
from app.providers.http import HttpFetcher

logger = get_logger(__name__)

STAT_GOV_BASE = "https://stat.gov.kz"
PUBLICATIONS_PATH = "/ru/industries/economy/prices/publications/"

#: <a href="509832/" class="release-title">Инфляция … (июль 2026г.)</a>
_LINK_RE = re.compile(
    r'<a\s+href="([^"]+)"\s+class="release-title"\s*>(.*?)</a>',
    re.S | re.I,
)
#: "…в июле 2026 года составила 10,2%"
_RATE_RE = re.compile(r"состав(?:ила|ил)\s*([0-9]+[.,][0-9]+)\s*%")
#: "(июль 2026г.)"
_PERIOD_RE = re.compile(r"\(([а-яё]+)\s*(\d{4})", re.I)

_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11,
    "декабр": 12,
}


def _parse_period(title: str) -> date | None:
    """Turn "(июль 2026г.)" into the last day of that month."""
    match = _PERIOD_RE.search(title)
    if not match:
        return None
    name, year_text = match.group(1).lower(), match.group(2)
    month = None
    for stem, number in _MONTHS.items():
        if name.startswith(stem):
            month = number
            break
    if month is None:
        return None
    year = int(year_text)
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


class StatGovInflationCollector:
    """Fetches the latest official CPI print and stores it."""

    source_name = "stat.gov.kz"

    def __init__(self, session: Session, *, base_url: str = STAT_GOV_BASE, timeout: float = 30.0):
        self.session = session
        self.base_url = base_url.rstrip("/")
        self._http = HttpFetcher(self.base_url, timeout=timeout)

    async def fetch_latest(self) -> dict:
        """Read the newest bulletin. Returns a report; never raises on network."""
        index = await self._http.fetch(PUBLICATIONS_PATH)
        if not index.ok or not index.text:
            return _failure(f"Индекс публикаций недоступен: {index.error}")

        target = self._newest_inflation_link(index.text)
        if target is None:
            return _failure(
                "На странице публикаций не найден бюллетень по инфляции. "
                "Вероятно, изменилась верстка stat.gov.kz."
            )
        href, title = target
        period_end = _parse_period(title)

        url = href if href.startswith("http") else f"{self.base_url}{PUBLICATIONS_PATH}{href.lstrip('/')}"
        detail = await self._http.fetch(url)
        if not detail.ok or not detail.text:
            return _failure(f"Бюллетень недоступен: {detail.error}", url=url)

        rate = self._extract_rate(detail.text)
        if rate is None:
            return _failure(
                "Бюллетень загружен, но извлечь значение инфляции не удалось.",
                url=url,
            )
        if not 0.0 <= rate <= 1.0:
            # A CPI print outside 0-100 % annual is a parsing failure, not news.
            return _failure(
                f"Извлечено неправдоподобное значение инфляции: {rate:.1%}.",
                url=url,
            )

        stored = self._store(
            rate=rate,
            period_end=period_end or date.today(),
            url=url,
            title=title.strip(),
            fetched_at=detail.fetched_at,
        )
        return {
            "ok": True,
            "annual_rate": rate,
            "annual_rate_pct": round(rate * 100, 2),
            "period_end": (period_end or date.today()).isoformat(),
            "source": self.source_name,
            "source_url": url,
            "title": title.strip(),
            "written": stored,
        }

    def _newest_inflation_link(self, html: str) -> tuple[str, str] | None:
        """First bulletin whose title names a month - the index is newest-first."""
        for href, raw_title in _LINK_RE.findall(html):
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw_title)).strip()
            if "инфляц" in title.lower() and _PERIOD_RE.search(title):
                return href, title
        return None

    def _extract_rate(self, html: str) -> float | None:
        text = re.sub(r"<[^>]+>", " ", html)
        text = text.replace("\xa0", " ")
        match = _RATE_RE.search(text)
        if not match:
            return None
        try:
            return float(match.group(1).replace(",", ".")) / 100.0
        except ValueError:
            return None

    def _store(
        self, *, rate: float, period_end: date, url: str, title: str, fetched_at
    ) -> bool:
        existing = self.session.execute(
            select(InflationData).where(
                InflationData.country == "KZ",
                InflationData.kind == "official",
                InflationData.period_end == period_end,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = InflationData(
                country="KZ", kind="official", period_end=period_end
            )
            self.session.add(existing)
        existing.annual_rate = rate
        existing.note = title
        existing.source = self.source_name
        existing.source_url = url
        existing.source_timestamp = fetched_at
        existing.fetched_at = fetched_at or datetime.now(timezone.utc)
        self.session.commit()
        logger.info(
            "official inflation stored: %.2f%% for period ending %s",
            rate * 100,
            period_end,
        )
        return True

    async def aclose(self) -> None:
        await self._http.aclose()


def set_manual_inflation(
    session: Session,
    rate: float,
    *,
    period_end: date | None = None,
    note: str | None = None,
) -> InflationData:
    """Operator override, used when the parser breaks or a print is fresh."""
    period_end = period_end or date.today()
    row = InflationData(
        country="KZ",
        kind="manual",
        period_end=period_end,
        annual_rate=rate,
        note=note or "Значение задано вручную.",
        source="manual",
        fetched_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.commit()
    return row


def _failure(detail: str, *, url: str | None = None) -> dict:
    logger.warning("inflation collection failed: %s", detail)
    return {"ok": False, "detail": detail, "source_url": url}
