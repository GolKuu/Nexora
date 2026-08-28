"""Capture and backfill the official one-year KASE chart universe.

Screenshots are audit artefacts only.  Numeric history always comes from the
public structured chart response used by kase.kz itself and is stored through
the existing validated history tables.

    python -m app.jobs.capture_kase_1y_charts --all --resume
    python -m app.jobs.capture_kase_1y_charts --ticker AIRA
    python -m app.jobs.capture_kase_1y_charts --bonds --data-only --only-missing
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.browser.session import BrowserService, BrowserUnavailableError
from app.collectors.kase_stock_catalog import (
    CATALOG_URL as STOCK_CATALOG_URL,
    KaseStockCatalogCollector,
)
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.bond import Bond
from app.models.history import BackfillCheckpoint, DailyMarketSnapshot
from app.models.instrument import Instrument, SHARE_INSTRUMENT_TYPES
from app.models.issuer import Issuer
from app.models.stock import Stock
from app.providers.kase_public_api import KasePublicApiProvider
from app.services.backfill.chart_api import KaseChartHistoryClient
from app.services.backfill.coverage import CoverageService
from app.services.backfill.store import HistoryStore
from app.services.backfill.validate import validate_observations
from app.services.backfill.window import BackfillWindow, backfill_window

CAPTURE_ROOT = Path("data/kase_1y_capture")
REPORT_PATH = Path("docs/kase-1y-capture-report.md")
BOND_CATALOG_URL = "https://kase.kz/api/instruments/securities/?sec_type=bond"
JOB_TYPE = "kase_1y_capture"
COLLECTOR_VERSION = "kase-1y-v1"

ERRORS = {
    "NO_PUBLIC_INSTRUMENT_PAGE", "NO_CHART", "NO_1Y_SELECTOR",
    "NO_PUBLIC_HISTORY", "IDENTITY_MISMATCH", "PAGE_TIMEOUT",
    "PARSER_CHANGED", "DATA_VALIDATION_FAILED", "ACCESS_RESTRICTED",
    "INSTRUMENT_DELISTED", "UNKNOWN_ERROR",
}


@dataclass(slots=True)
class ManifestEntry:
    ticker: str
    isin: str | None
    name: str
    issuer: str
    instrument_type: str
    instrument_subtype: str | None
    currency: str
    catalog_url: str
    instrument_url: str | None
    is_active: bool = True
    capture_status: str = "PENDING"
    history_status: str = "PENDING"
    chart_status: str = "PENDING"
    error_reason: str | None = None
    data_points: int = 0
    actual_start: str | None = None
    actual_end: str | None = None
    coverage_days: int = 0
    coverage_percent: float | None = None
    insufficient_history: bool = False
    screenshot_path: str | None = None


def _safe(value: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "NOISIN").strip("._")
    return cleaned or "UNKNOWN"


def _entry_key(entry: ManifestEntry) -> tuple[str, str]:
    return entry.instrument_type, entry.ticker.upper()


def _issuer(session: Session, code: str, name: str, *, source_url: str) -> Issuer:
    row = session.scalar(select(Issuer).where(func.upper(Issuer.code) == code.upper()))
    now = datetime.now(timezone.utc)
    if row is None:
        row = Issuer(code=code, name=name, short_name=name, country="KZ", is_active=True)
        session.add(row)
        session.flush()
    row.name = name or row.name
    row.is_active = True
    row.source = "kase_public_api"
    row.source_url = source_url
    row.fetched_at = now
    return row


def _ensure_bond(session: Session, dto) -> tuple[Bond, Instrument]:
    """Reuse Bond and add its canonical Instrument identity for shared history."""
    issuer = _issuer(
        session, dto.issuer_code or dto.ticker, dto.name or dto.issuer_code or dto.ticker,
        source_url=BOND_CATALOG_URL,
    )
    bond = session.scalar(select(Bond).where(func.upper(Bond.ticker) == dto.ticker.upper()))
    if bond is None:
        bond = Bond(ticker=dto.ticker, issuer_id=issuer.id, name=dto.name or dto.ticker)
        session.add(bond)
    for field in (
        "isin", "name", "currency", "nominal", "issue_date", "maturity_date",
        "coupon_rate", "coupon_type", "coupon_frequency", "next_coupon_date",
        "day_count", "issue_size", "outstanding_amount", "market_segment",
        "bond_type", "secured", "subordinated", "callable", "putable",
        "guarantee", "kase_url", "is_active",
    ):
        value = getattr(dto, field, None)
        if value is not None or field in {"isin", "is_active"}:
            setattr(bond, field, value)
    bond.source = "kase_public_api"
    bond.source_url = BOND_CATALOG_URL
    bond.source_timestamp = dto.provenance.source_timestamp if dto.provenance else None
    bond.fetched_at = datetime.now(timezone.utc)
    session.flush()

    instrument = session.scalar(
        select(Instrument).where(
            Instrument.instrument_type == "bond",
            func.upper(Instrument.ticker) == dto.ticker.upper(),
        )
    )
    if instrument is None:
        instrument = Instrument(
            ticker=dto.ticker, issuer_id=issuer.id, instrument_type="bond"
        )
        session.add(instrument)
    instrument.isin = dto.isin
    instrument.security_type = dto.bond_type
    instrument.currency = dto.currency or "KZT"
    instrument.market_segment = dto.market_segment
    instrument.listing_status = "active" if dto.is_active else "inactive"
    instrument.kase_url = dto.kase_url
    instrument.is_active = bool(dto.is_active)
    instrument.source = "kase_public_api"
    instrument.source_url = BOND_CATALOG_URL
    instrument.source_timestamp = dto.provenance.source_timestamp if dto.provenance else None
    instrument.fetched_at = datetime.now(timezone.utc)
    session.flush()
    return bond, instrument


def _ensure_stock(session: Session, item: dict) -> tuple[Stock, Instrument]:
    """Inventory-only upsert; it deliberately does not create change events."""
    issuer = _issuer(
        session, item["issuer_code"], item["issuer"], source_url=STOCK_CATALOG_URL
    )
    instrument = session.scalar(select(Instrument).where(
        Instrument.instrument_type == item["instrument_type"],
        func.upper(Instrument.ticker) == item["ticker"].upper(),
    ))
    if instrument is None:
        instrument = Instrument(
            ticker=item["ticker"], issuer_id=issuer.id,
            instrument_type=item["instrument_type"],
        )
        session.add(instrument)
    instrument.isin = item["isin"]
    instrument.security_type = item["security_type"]
    instrument.currency = item["currency"]
    instrument.market_segment = item["market_segment"]
    instrument.listing_status = item["listing_status"]
    instrument.kase_url = f"https://kase.kz/ru/investors/shares/{item['ticker']}"
    instrument.is_active = item["is_active"]
    instrument.source = "kase_public_website"
    instrument.source_url = STOCK_CATALOG_URL
    instrument.source_timestamp = item["source_timestamp"]
    instrument.fetched_at = datetime.now(timezone.utc)
    session.flush()
    stock = session.scalar(select(Stock).where(Stock.instrument_id == instrument.id))
    if stock is None:
        stock = Stock(
            instrument_id=instrument.id, share_class=item["share_class"],
            listing_date=item["listing_date"], lot_size=1,
        )
        session.add(stock)
        session.flush()
    return stock, instrument


async def discover_universe(
    session: Session, *, stocks: bool, bonds: bool, provider: KasePublicApiProvider
) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    if stocks:
        collector = KaseStockCatalogCollector(session)
        for item in await collector.fetch():
            _ensure_stock(session, item)
        rows = session.execute(
            select(Instrument, Issuer)
            .join(Issuer, Issuer.id == Instrument.issuer_id)
            .where(Instrument.instrument_type.in_(SHARE_INSTRUMENT_TYPES))
            .order_by(Instrument.ticker)
        ).all()
        entries.extend(
            ManifestEntry(
                ticker=i.ticker, isin=i.isin, name=issuer.short_name or issuer.name,
                issuer=issuer.name, instrument_type="stock",
                instrument_subtype=i.security_type, currency=i.currency,
                catalog_url=STOCK_CATALOG_URL, instrument_url=i.kase_url,
                is_active=i.is_active,
            )
            for i, issuer in rows
        )
    if bonds:
        for dto in await provider.get_bonds():
            bond, _instrument = _ensure_bond(session, dto)
            entries.append(
                ManifestEntry(
                    ticker=bond.ticker, isin=bond.isin, name=bond.name,
                    issuer=bond.issuer.name, instrument_type="bond",
                    instrument_subtype=bond.bond_type, currency=bond.currency,
                    catalog_url=BOND_CATALOG_URL, instrument_url=bond.kase_url,
                    is_active=bond.is_active,
                )
            )
    session.commit()
    unique = {_entry_key(item): item for item in entries}
    return [unique[key] for key in sorted(unique)]


def write_manifest(entries: Iterable[ManifestEntry], root: Path = CAPTURE_ROOT) -> None:
    root.mkdir(parents=True, exist_ok=True)
    rows = [asdict(item) for item in entries]
    json_path = root / "instruments.json"
    json_tmp = root / "instruments.json.tmp"
    json_tmp.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _replace_with_retry(json_tmp, json_path)
    csv_path = root / "instruments.csv"
    csv_tmp = root / "instruments.csv.tmp"
    with csv_tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ManifestEntry.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(rows)
    _replace_with_retry(csv_tmp, csv_path)


def _replace_with_retry(source: Path, target: Path) -> None:
    """Atomically publish an artefact despite short-lived Windows file locks."""
    for attempt in range(5):
        try:
            source.replace(target)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


def load_manifest(root: Path = CAPTURE_ROOT) -> dict[tuple[str, str], dict]:
    path = root / "instruments.json"
    if not path.exists():
        return {}
    return {
        (row["instrument_type"], row["ticker"].upper()): row
        for row in json.loads(path.read_text(encoding="utf-8"))
    }


def restore_history_status(session: Session, entry: ManifestEntry) -> None:
    """Rebuild manifest state from authoritative committed DB coverage."""
    type_filter = (
        Instrument.instrument_type.in_(SHARE_INSTRUMENT_TYPES)
        if entry.instrument_type == "stock"
        else Instrument.instrument_type == entry.instrument_type
    )
    instrument = session.scalar(select(Instrument).where(
        type_filter, func.upper(Instrument.ticker) == entry.ticker.upper(),
    ))
    if instrument is None:
        return
    coverage = CoverageService(session).get(instrument.id, job_type=JOB_TYPE)
    if coverage is None:
        return
    entry.history_status = coverage.status.upper()
    entry.actual_start = coverage.actual_market_start.isoformat() if coverage.actual_market_start else None
    entry.actual_end = coverage.actual_market_end.isoformat() if coverage.actual_market_end else None
    entry.coverage_days = coverage.market_days_covered or 0
    expected = coverage.market_days_expected or 0
    entry.coverage_percent = round(entry.coverage_days / expected * 100, 2) if expected else None
    entry.insufficient_history = coverage.status != "complete"
    entry.data_points = session.scalar(select(func.count()).select_from(DailyMarketSnapshot).where(
        DailyMarketSnapshot.instrument_id == instrument.id,
        DailyMarketSnapshot.trading_date >= coverage.requested_start.date(),
        DailyMarketSnapshot.trading_date <= coverage.requested_end.date(),
    )) or 0
    # Coverage is authoritative for numeric history, but it must not overwrite
    # a separately verified screenshot outcome such as NO_CHART.
    if entry.capture_status == "PENDING":
        entry.chart_status = "DATA_READY" if entry.data_points else "NO_HISTORY"
    if coverage.status == "unavailable" and entry.error_reason is None:
        entry.error_reason = "NO_PUBLIC_HISTORY"


def _checkpoint(session: Session, instrument_id: int) -> BackfillCheckpoint:
    row = session.scalar(select(BackfillCheckpoint).where(
        BackfillCheckpoint.job_type == JOB_TYPE,
        BackfillCheckpoint.instrument_id == instrument_id,
    ))
    if row is None:
        row = BackfillCheckpoint(job_type=JOB_TYPE, instrument_id=instrument_id)
        session.add(row)
        session.flush()
    return row


def record_delisted(session: Session, entry: ManifestEntry, window: BackfillWindow) -> None:
    instrument = session.scalar(select(Instrument).where(
        Instrument.instrument_type == entry.instrument_type,
        func.upper(Instrument.ticker) == entry.ticker.upper(),
    ))
    if instrument is None:
        return
    CoverageService(session).measure(
        instrument, window, job_type=JOB_TYPE, status="unavailable",
        details={"reason": "INSTRUMENT_DELISTED", "source": "KASE Public Web"},
    )
    checkpoint = _checkpoint(session, instrument.id)
    checkpoint.range_start, checkpoint.range_end = window.start, window.end
    checkpoint.status = "completed"
    checkpoint.last_error = "INSTRUMENT_DELISTED"
    session.commit()


async def collect_history(
    session: Session, entry: ManifestEntry, window: BackfillWindow,
    client: KaseChartHistoryClient,
) -> None:
    type_filter = (
        Instrument.instrument_type.in_(SHARE_INSTRUMENT_TYPES)
        if entry.instrument_type == "stock"
        else Instrument.instrument_type == entry.instrument_type
    )
    instrument = session.scalar(select(Instrument).where(
        type_filter, func.upper(Instrument.ticker) == entry.ticker.upper(),
    ))
    if instrument is None:
        entry.history_status = "FAILED"
        entry.error_reason = "DATA_VALIDATION_FAILED"
        return
    checkpoint = _checkpoint(session, instrument.id)
    checkpoint.status = "processing"
    checkpoint.attempts += 1
    checkpoint.range_start, checkpoint.range_end = window.start, window.end
    session.commit()
    try:
        records = await client.daily_history(entry.ticker, window)
        if not records:
            entry.history_status = "UNAVAILABLE"
            entry.chart_status = "NO_HISTORY"
            entry.error_reason = "NO_PUBLIC_HISTORY"
            coverage = CoverageService(session).measure(
                instrument, window, job_type=JOB_TYPE, status="unavailable",
                details={"price_unit": "%_of_nominal" if entry.instrument_type == "bond" else entry.currency},
            )
            checkpoint.status = "partial"
            checkpoint.last_error = entry.error_reason
            session.commit()
            return
        outcome = validate_observations(records)
        store = HistoryStore(session, parser_version=COLLECTOR_VERSION)
        if outcome.rejections:
            store.record_anomalies(
                instrument_id=instrument.id, ticker=entry.ticker, job_type=JOB_TYPE,
                rejections=outcome.rejections, source="kase_public_chart_api",
                source_url=records[0].source_url,
            )
        if outcome.batch_rejected:
            entry.history_status = "FAILED"
            entry.error_reason = "DATA_VALIDATION_FAILED"
            checkpoint.status = "failed"
            checkpoint.last_error = entry.error_reason
            session.commit()
            return
        store.save_observations(instrument.id, outcome.accepted)
        store.rebuild_daily_snapshots(
            instrument.id, start=window.start_date, end=window.end_date
        )
        coverage = CoverageService(session).measure(
            instrument, window, job_type=JOB_TYPE,
            details={
                "source": "KASE Public Web", "data_mode": "browser_visible_structured",
                "price_unit": "%_of_nominal" if entry.instrument_type == "bond" else entry.currency,
            },
        )
        entry.data_points = len(outcome.accepted)
        entry.actual_start = coverage.actual_market_start.isoformat() if coverage.actual_market_start else None
        entry.actual_end = coverage.actual_market_end.isoformat() if coverage.actual_market_end else None
        entry.coverage_days = coverage.market_days_covered or 0
        expected = coverage.market_days_expected or 0
        entry.coverage_percent = round(entry.coverage_days / expected * 100, 2) if expected else None
        entry.insufficient_history = coverage.status != "complete"
        entry.history_status = coverage.status.upper()
        entry.chart_status = "DATA_READY"
        checkpoint.status = "completed" if coverage.status == "complete" else "partial"
        checkpoint.last_processed_timestamp = coverage.actual_market_end
        checkpoint.last_error = None
        session.commit()
    except Exception as exc:
        session.rollback()
        checkpoint = _checkpoint(session, instrument.id)
        checkpoint.status = "failed"
        checkpoint.last_error = str(exc)[:2000]
        entry.history_status = "FAILED"
        entry.error_reason = "UNKNOWN_ERROR"
        session.commit()


async def capture_chart(session, entry: ManifestEntry, root: Path) -> None:
    if not entry.instrument_url:
        entry.capture_status = "FAILED"
        entry.error_reason = entry.error_reason or "NO_PUBLIC_INSTRUMENT_PAGE"
        return
    result = await session.open_url(entry.instrument_url)
    if not result.ok:
        entry.capture_status = "FAILED"
        entry.error_reason = entry.error_reason or "PAGE_TIMEOUT"
        return
    await session.wait_for_content(min_chars=150)
    page = session.page
    body = (await page.locator("body").inner_text()).upper()
    if entry.ticker.upper() not in body and (not entry.isin or entry.isin.upper() not in body):
        entry.capture_status = "FAILED"
        entry.error_reason = entry.error_reason or "IDENTITY_MISMATCH"
        return

    selectors = (
        "[data-testid*='chart']", "[class*='tv-lightweight-charts']",
        "[class*='chart-container']", "[class*='Chart']", "canvas",
    )
    chart = None
    chart_selector = None
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(min(await locator.count(), 20)):
            candidate = locator.nth(index)
            box = await candidate.bounding_box() if await candidate.is_visible() else None
            if box and box["width"] >= 420 and box["height"] >= 160:
                chart, chart_selector = candidate, selector
                break
        if chart is not None:
            break
    if chart is None:
        entry.capture_status = "UNAVAILABLE"
        entry.chart_status = "NO_CHART"
        entry.error_reason = entry.error_reason or "NO_CHART"
        return

    one_year = page.get_by_text(re.compile(r"^(1Y|1\s*ГОД|ГОД|12M)$", re.I))
    clicked = False
    for index in range(await one_year.count()):
        candidate = one_year.nth(index)
        if await candidate.is_visible():
            await candidate.click()
            clicked = True
            break
    if not clicked:
        entry.capture_status = "UNAVAILABLE"
        entry.chart_status = "NO_1Y_SELECTOR"
        entry.error_reason = entry.error_reason or "NO_1Y_SELECTOR"
        return
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        await page.wait_for_timeout(500)

    folder = root / "screenshots" / ("stocks" if entry.instrument_type == "stock" else "bonds")
    folder.mkdir(parents=True, exist_ok=True)
    shot = folder / f"{_safe(entry.ticker)}__{_safe(entry.isin)}__1Y.png"
    await chart.screenshot(path=str(shot))
    box = await chart.bounding_box()
    if not shot.exists() or shot.stat().st_size < 2_000 or not box:
        entry.capture_status = "FAILED"
        entry.error_reason = entry.error_reason or "NO_CHART"
        return
    entry.screenshot_path = shot.as_posix()
    entry.capture_status = "SUCCESS"
    entry.chart_status = "SUCCESS"
    meta_dir = root / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "ticker": entry.ticker, "ISIN": entry.isin,
        "instrument_type": entry.instrument_type, "instrument_url": entry.instrument_url,
        "source": "KASE Public Web", "captured_at": datetime.now(timezone.utc).isoformat(),
        "requested_range": "1Y", "actual_start": entry.actual_start,
        "actual_end": entry.actual_end, "screenshot_path": entry.screenshot_path,
        "viewport": {"width": settings.BROWSER_VIEWPORT_WIDTH, "height": settings.BROWSER_VIEWPORT_HEIGHT},
        "browser_version": session.browser_version, "collector_version": COLLECTOR_VERSION,
        "chart_selector": chart_selector, "chart_status": entry.chart_status,
        "history_status": entry.history_status, "notes": None,
    }
    (meta_dir / f"{_safe(entry.ticker)}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_report(entries: list[ManifestEntry], root: Path = CAPTURE_ROOT) -> dict:
    captured_at = datetime.now(timezone.utc).isoformat()
    def stats(kind: str) -> dict:
        rows = [x for x in entries if x.instrument_type == kind]
        return {
            "discovered": len(rows),
            "screenshots_completed": sum(x.capture_status == "SUCCESS" for x in rows),
            "no_chart": sum(x.chart_status in {"NO_CHART", "NO_1Y_SELECTOR"} for x in rows),
            "history_completed": sum(x.history_status == "COMPLETE" for x in rows),
            "partial": sum(x.history_status == "PARTIAL" for x in rows),
            "unavailable": sum(x.history_status == "UNAVAILABLE" for x in rows),
            "failed": sum(x.history_status == "FAILED" or x.capture_status == "FAILED" for x in rows),
        }
    report = {
        "captured_at": captured_at, "definition_of_all": "official configured KASE catalog at capture time",
        "stocks": stats("stock"), "bonds": stats("bond"),
        "instruments": [asdict(x) for x in entries],
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# KASE 1Y capture report", "", f"Captured at: `{captured_at}`", "",
        "`ALL` means every instrument returned by the configured official KASE catalogs at that timestamp.", "",
        "| Type | Discovered | Screenshots | No chart | Complete history | Partial | Unavailable | Failed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for kind, label in (("stocks", "Stocks"), ("bonds", "Bonds")):
        s = report[kind]
        lines.append(f"| {label} | {s['discovered']} | {s['screenshots_completed']} | {s['no_chart']} | {s['history_completed']} | {s['partial']} | {s['unavailable']} | {s['failed']} |")
    lines.extend(["", "| Ticker | ISIN | Type | Screenshot | Coverage | Points | Status | Error |", "|---|---|---|---|---:|---:|---|---|"])
    for row in entries:
        lines.append(
            f"| {row.ticker} | {row.isin or '—'} | {row.instrument_type} | "
            f"{row.capture_status} | {row.coverage_percent if row.coverage_percent is not None else '—'} | "
            f"{row.data_points} | {row.history_status} | {row.error_reason or '—'} |"
        )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


async def run(args: argparse.Namespace) -> int:
    root = Path(args.output)
    previous = load_manifest(root)
    session = SessionLocal()
    provider = KasePublicApiProvider()
    browser_service = BrowserService() if not args.data_only else None
    browser_session = None
    try:
        discovered_entries = await discover_universe(
            session, stocks=args.stocks or args.all, bonds=args.bonds or args.all,
            provider=provider,
        )
        selected_kinds: set[str] = set()
        if args.stocks or args.all:
            selected_kinds.add("stock")
        if args.bonds or args.all:
            selected_kinds.add("bond")
        preserved = [
            ManifestEntry(**{
                field: row.get(field)
                for field in ManifestEntry.__dataclass_fields__
                if field in row
            })
            for (kind, _ticker), row in previous.items()
            if kind not in selected_kinds
        ]
        manifest_entries = discovered_entries + preserved
        entries = discovered_entries
        if args.ticker:
            entries = [x for x in manifest_entries if x.ticker.upper() == args.ticker.upper()]
        if not entries:
            print("No matching instruments were discovered.")
            return 1
        for entry in manifest_entries:
            old = previous.get(_entry_key(entry))
            if old:
                for field in ("capture_status", "history_status", "chart_status", "error_reason", "data_points", "actual_start", "actual_end", "coverage_days", "coverage_percent", "insufficient_history", "screenshot_path"):
                    setattr(entry, field, old.get(field, getattr(entry, field)))
            restore_history_status(session, entry)
        write_manifest(manifest_entries, root)
        window = backfill_window(years=1)
        history_client = KaseChartHistoryClient()
        if browser_service:
            try:
                browser_session = await browser_service.new_session(label=JOB_TYPE)
            except BrowserUnavailableError as exc:
                print(f"Browser unavailable: {exc}")
        total = len(entries)
        for index, entry in enumerate(entries, 1):
            done_history = entry.history_status in {"COMPLETE", "PARTIAL", "UNAVAILABLE"}
            done_capture = entry.capture_status == "SUCCESS"
            if not entry.is_active:
                entry.history_status = "UNAVAILABLE"
                entry.capture_status = "UNAVAILABLE"
                entry.chart_status = "NO_CHART"
                entry.error_reason = "INSTRUMENT_DELISTED"
                done_history = True
                done_capture = True
                record_delisted(session, entry, window)
            if entry.is_active and not args.screenshots_only and not ((args.resume or args.only_missing) and done_history):
                if index > 1 and settings.BACKFILL_REQUEST_DELAY_MS > 0:
                    await asyncio.sleep(settings.BACKFILL_REQUEST_DELAY_MS / 1000)
                await collect_history(session, entry, window, history_client)
            if entry.is_active and not args.data_only and browser_session and not ((args.resume or args.only_missing) and done_capture):
                try:
                    await capture_chart(browser_session, entry, root)
                except Exception as exc:
                    entry.capture_status = "FAILED"
                    entry.error_reason = entry.error_reason or "UNKNOWN_ERROR"
                    print(f"capture {entry.ticker}: {exc}")
            # DB coverage/checkpoints are committed per instrument. Publishing
            # the large human-readable manifest in batches avoids thousands of
            # Windows file-lock races while bounding file-only resume loss.
            if index % 25 == 0 or index == total:
                write_manifest(manifest_entries, root)
            counts = {status: sum(x.history_status == status for x in manifest_entries) for status in ("COMPLETE", "PARTIAL", "UNAVAILABLE", "FAILED")}
            print(f"[{index}/{total}] {entry.ticker}: screenshot={entry.capture_status} history={entry.history_status}; complete={counts['COMPLETE']} partial={counts['PARTIAL']} unavailable={counts['UNAVAILABLE']} failed={counts['FAILED']} remaining={total-index}")
        report = write_report(manifest_entries, root)
        print(json.dumps({"stocks": report["stocks"], "bonds": report["bonds"]}, ensure_ascii=False))
        return 0
    finally:
        if browser_service:
            await browser_service.aclose()
        await provider.aclose()
        session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.capture_kase_1y_charts")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--stocks", action="store_true")
    scope.add_argument("--bonds", action="store_true")
    scope.add_argument("--all", action="store_true")
    parser.add_argument("--ticker")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--only-missing", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--screenshots-only", action="store_true")
    mode.add_argument("--data-only", action="store_true")
    parser.add_argument("--output", default=str(CAPTURE_ROOT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.stocks or args.bonds or args.all):
        args.all = True
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
