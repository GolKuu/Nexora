"""Current Kazakhstan government bonds from the public KASE catalog.

Corporate bonds and government securities are separate instrument classes in
the KASE API (``sec_type=bond`` and ``sec_type=gsec``).  The main snapshot was
historically built from the former only, which made the Government filter
truthfully return an empty list.  This lightweight collector keeps a bounded
set of traded Kazakhstan government issues in the product database.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from statistics import median

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import BondType, CouponType, DataMode
from app.core.logging import get_logger
from app.models.market import BondQuote
from app.repositories.bonds import BondRepository
from app.repositories.issuers import IssuerRepository
from app.services.metrics_service import MetricsService
from app.services.peer_service import PeerService
from app.services.scoring_service import ScoringService

logger = get_logger(__name__)

CATALOG_URL = "https://kase.kz/api/instruments/securities/"
COUPON_URL = "https://kase.kz/api/instruments/coupon-payments/{ticker}/"
SOURCE = "kase_public_api"
MAX_ISSUES = 40
DOMESTIC_SOVEREIGN_ISSUERS = {"MFRK", "NBRK"}


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _day(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _moment(value, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return fallback


def _localized(row: dict, field: str) -> str | None:
    for language in ("ru", "en", "kz"):
        value = row.get(f"{field}_{language}")
        if value:
            return str(value).strip()
    value = row.get(field)
    return str(value).strip() if value else None


def _coupon_frequency(schedule: list[dict]) -> int | None:
    dates = sorted(filter(None, (_day(row.get("start_date")) for row in schedule)))
    if len(dates) < 2:
        return None
    gap = median((right - left).days for left, right in zip(dates, dates[1:]))
    if gap <= 45:
        return 12
    if gap <= 120:
        return 4
    if gap <= 220:
        return 2
    if gap <= 400:
        return 1
    return None


def _coupon_details(row: dict, schedule: list[dict]) -> tuple[float | None, str, int | None, date | None]:
    ticker = row.get("ticker") or {}
    kind = (_localized(ticker, "typesec") or "").casefold()
    coupon_type = CouponType.ZERO.value if "discount" in kind or "дисконт" in kind else CouponType.FIXED.value
    today = date.today()
    dated = sorted(
        ((payment, _day(payment.get("start_date"))) for payment in schedule),
        key=lambda item: item[1] or date.max,
    )
    upcoming = next((item for item in dated if item[1] and item[1] >= today), None)
    applicable = upcoming[0] if upcoming else (dated[-1][0] if dated else {})
    rate = _number(applicable.get("total_rate"))
    if rate is None:
        rate = _number(applicable.get("rate"))
    if rate is None:
        rate = _number(ticker.get("cupon"))
    if coupon_type == CouponType.ZERO.value:
        rate = 0.0
    return (
        None if rate is None else rate / 100.0,
        coupon_type,
        _coupon_frequency(schedule),
        upcoming[1] if upcoming else None,
    )


async def _fetch_schedule(client: httpx.AsyncClient, ticker: str) -> list[dict]:
    try:
        response = await client.get(COUPON_URL.format(ticker=ticker))
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []
    except (httpx.HTTPError, ValueError):
        return []


def _eligible(row: dict) -> bool:
    maturity = _day(row.get("repayment_start_date"))
    issuer = str(row.get("org_code") or "").strip().upper()
    has_market_data = any(
        row.get(field) is not None
        for field in ("price", "close_price", "best_bid", "best_offer", "dohod_total")
    )
    return (
        row.get("sec_type") == "gsec"
        and issuer in DOMESTIC_SOVEREIGN_ISSUERS
        and maturity is not None
        and maturity >= date.today()
        and has_market_data
    )


async def refresh_government_market(
    session: Session,
    *,
    client: httpx.AsyncClient | None = None,
    limit: int = MAX_ISSUES,
) -> dict:
    """Upsert a bounded, market-active set of real Kazakhstan government bonds."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=60, follow_redirects=True)
    fetched_at = datetime.now(timezone.utc)
    try:
        response = await client.get(CATALOG_URL, params={"sec_type": "gsec"})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("KASE government catalog is not a list")

        rows = [row for row in payload if isinstance(row, dict) and _eligible(row)]
        rows.sort(
            key=lambda row: (
                _number(row.get("volkzt")) or 0.0,
                _number(row.get("dealcnt")) or 0.0,
                str(row.get("date0") or ""),
            ),
            reverse=True,
        )
        rows = rows[: max(1, min(limit, MAX_ISSUES))]
        schedules = await asyncio.gather(
            *(_fetch_schedule(client, str(row.get("code"))) for row in rows)
        )

        issuers = IssuerRepository(session)
        bonds = BondRepository(session)
        metrics = MetricsService(session)
        scoring = ScoringService(session)
        peers = PeerService(session)
        changed = []

        for row, schedule in zip(rows, schedules):
            ticker = str(row.get("code") or "").strip()
            issuer_code = str(row.get("org_code") or "").strip().upper()
            ticker_data = row.get("ticker") or {}
            maturity = _day(row.get("repayment_start_date"))
            volume = _number(row.get("volume_release"))
            count = _number(row.get("volume_release_number"))
            nominal = volume / count if volume and count else None
            coupon_rate, coupon_type, frequency, next_coupon = _coupon_details(row, schedule)
            source_url = f"https://kase.kz/ru/investors/gsecs/{ticker}"
            day_count = "ACT/360" if str(ticker_data.get("basis") or "") == "360" else "ACT/365F"

            issuer = issuers.upsert(
                issuer_code,
                {
                    "name": _localized(row, "org_name") or issuer_code,
                    "short_name": _localized(row, "org_short_name"),
                    "country": "KZ",
                    "sector": "sovereign",
                    "is_financial_institution": False,
                    "is_state_owned": True,
                    "kase_url": f"https://kase.kz/ru/issuers/{issuer_code}",
                    "source": SOURCE,
                    "source_identifier": issuer_code,
                    "source_url": CATALOG_URL,
                    "source_timestamp": _moment(row.get("date0"), fetched_at),
                    "fetched_at": fetched_at,
                },
            )
            bond = bonds.upsert(
                ticker,
                {
                    "issuer_id": issuer.id,
                    "name": _localized(row, "subcategory_name") or _localized(row, "org_short_name") or ticker,
                    "isin": str(ticker_data.get("nin") or "").strip() or None,
                    "currency": str(row.get("currency_type") or "KZT").upper(),
                    "nominal": nominal,
                    "maturity_date": maturity,
                    "coupon_rate": coupon_rate,
                    "coupon_type": coupon_type,
                    "coupon_frequency": frequency,
                    "next_coupon_date": next_coupon,
                    "day_count": day_count,
                    "issue_size": volume,
                    "outstanding_amount": _number(row.get("volume")),
                    "market_segment": _localized(row, "board"),
                    "bond_type": BondType.GOVERNMENT.value,
                    "kase_url": source_url,
                    "is_active": True,
                    "source": SOURCE,
                    "source_identifier": ticker,
                    "source_url": source_url,
                    "source_timestamp": _moment(row.get("date0"), fetched_at),
                    "fetched_at": fetched_at,
                },
            )

            quote_timestamp = _moment(row.get("date0"), fetched_at)
            existing = session.execute(
                select(BondQuote).where(
                    BondQuote.bond_id == bond.id,
                    BondQuote.timestamp == quote_timestamp,
                )
            ).scalar_one_or_none()
            if existing is None:
                clean = _number(row.get("price")) or _number(row.get("close_price"))
                ytm = _number(row.get("dohod_total"))
                if ytm is None:
                    ytm = _number(row.get("dohod"))
                session.add(
                    BondQuote(
                        bond_id=bond.id,
                        timestamp=quote_timestamp,
                        bid=_number(row.get("best_bid")),
                        ask=_number(row.get("best_offer")),
                        last=clean,
                        clean_price=clean,
                        ytm=None if ytm is None else ytm / 100.0,
                        volume=_number(row.get("volume_number")),
                        turnover=_number(row.get("volkzt")),
                        number_of_trades=int(_number(row.get("dealcnt")) or 0),
                        data_mode=DataMode.END_OF_DAY.value,
                        source=SOURCE,
                        source_identifier=ticker,
                        source_url=CATALOG_URL,
                        source_timestamp=quote_timestamp,
                        fetched_at=fetched_at,
                    )
                )
            session.flush()
            metrics.rebuild_cashflows(bond)
            metric = metrics.compute(bond)
            if metric is not None:
                peers.assign(bond, metric.years_to_maturity)
            changed.append(bond)

        session.flush()
        for bond in changed:
            scoring.compute(bond)
        session.commit()
        return {
            "status": "updated",
            "source": "KASE",
            "data_mode": DataMode.END_OF_DAY.value,
            "bonds": len(changed),
            "checked_at": fetched_at.isoformat(),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_client:
            await client.aclose()


async def ensure_government_market(session: Session) -> dict:
    """Best-effort startup refresh; a bundled snapshot remains the fallback."""
    try:
        return await refresh_government_market(session)
    except Exception as exc:
        logger.warning("KASE government market refresh failed: %s", exc)
        return {"status": "stale_error", "bonds": 0, "error": str(exc)}


__all__ = ["ensure_government_market", "refresh_government_market"]
