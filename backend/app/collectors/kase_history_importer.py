"""Offline importer for a user-licensed KASE deals-register CSV export."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.forecast.calendar import KASE_TZ, kase_date
from app.models.instrument import Instrument
from app.models.stock import Stock, StockQuote

SOURCE = "kase_licensed_archive"
SOURCE_URL = "https://kase.kz/en/information/archived-trade-information"
REQUIRED_COLUMNS = {"date", "time", "inst_type", "symbol", "price", "volume"}


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip().replace("\u00a0", "").replace(" ", "")
    if not cleaned:
        return None
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _date(value: str) -> date:
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported KASE date: {value!r}")


def _timestamp(day: date, value: str) -> datetime:
    parsed_time = None
    for pattern in ("%H:%M:%S", "%H:%M"):
        try:
            parsed_time = datetime.strptime(value.strip(), pattern).time()
            break
        except ValueError:
            continue
    if parsed_time is None:
        raise ValueError(f"unsupported KASE time: {value!r}")
    return datetime.combine(day, parsed_time, tzinfo=KASE_TZ)


def _normalise_headers(fieldnames: Iterable[str | None]) -> dict[str, str]:
    return {str(original).strip().lower(): str(original) for original in fieldnames if original is not None}


def _read_csv(path: Path) -> tuple[list[dict[str, str]], str]:
    raw = path.read_bytes()
    decoded = None
    encoding = ""
    for candidate in ("utf-8-sig", "cp1251"):
        try:
            decoded = raw.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("KASE CSV must be UTF-8 or Windows-1251")
    try:
        dialect = csv.Sniffer().sniff(decoded[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(decoded.splitlines(), dialect=dialect)
    headers = _normalise_headers(reader.fieldnames or [])
    missing = REQUIRED_COLUMNS - set(headers)
    if missing:
        raise ValueError(f"missing KASE archive columns: {', '.join(sorted(missing))}")
    rows = [{key.strip().lower(): (value or "").strip() for key, value in row.items() if key} for row in reader]
    return rows, encoding


@dataclass
class DailyBar:
    ticker: str
    day: date
    first_at: datetime
    last_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float
    trades: int

    def add(self, timestamp: datetime, price: float, volume: float, turnover: float) -> None:
        if timestamp < self.first_at:
            self.first_at, self.open = timestamp, price
        if timestamp >= self.last_at:
            self.last_at, self.close = timestamp, price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.volume += volume
        self.turnover += turnover
        self.trades += 1


def parse_deals_csv(path: str | Path) -> tuple[list[DailyBar], dict]:
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file() or source_path.suffix.lower() not in {".csv", ".txt"}:
        raise ValueError("provide an existing KASE deals-register CSV file")
    rows, encoding = _read_csv(source_path)
    bars: dict[tuple[str, date], DailyBar] = {}
    rejected = 0
    for row in rows:
        # Equity, regular-market, market deals only. Negotiated/special deals
        # are not representative daily-price observations for this model.
        if row.get("inst_type", "").upper() != "E":
            continue
        if row.get("t_type", "T").upper() not in {"", "T"} or row.get("market_sector", "1") not in {"", "1"}:
            continue
        ticker = row.get("symbol", "").strip().upper()
        price, volume = _number(row.get("price")), _number(row.get("volume"))
        if not ticker or price is None or price <= 0 or volume is None or volume < 0:
            rejected += 1
            continue
        try:
            day = _date(row["date"])
            timestamp = _timestamp(day, row["time"])
        except ValueError:
            rejected += 1
            continue
        turnover = _number(row.get("value_kzt"))
        turnover = turnover if turnover is not None and turnover >= 0 else price * volume
        key = (ticker, day)
        if key not in bars:
            bars[key] = DailyBar(ticker, day, timestamp, timestamp, price, price, price, price, volume, turnover, 1)
        else:
            bars[key].add(timestamp, price, volume, turnover)
    result = sorted(bars.values(), key=lambda bar: (bar.ticker, bar.day))
    return result, {"file": str(source_path), "encoding": encoding, "input_rows": len(rows),
                    "accepted_deals": sum(bar.trades for bar in result), "rejected_rows": rejected}


def import_deals_csv(session: Session, path: str | Path, *, dry_run: bool = True) -> dict:
    bars, metadata = parse_deals_csv(path)
    savepoint = session.begin_nested() if dry_run else None
    instruments = list(session.execute(select(Instrument, Stock).join(Stock, Stock.instrument_id == Instrument.id)).all())
    stocks = {instrument.ticker.upper(): stock for instrument, stock in instruments}
    existing_by_stock: dict[int, dict[date, StockQuote]] = {}
    for stock in stocks.values():
        rows = list(session.execute(select(StockQuote).where(StockQuote.stock_id == stock.id)).scalars())
        existing_by_stock[stock.id] = {kase_date(row.timestamp): row for row in rows}
    file_digest = hashlib.sha256(Path(path).expanduser().resolve().read_bytes()).hexdigest()
    created = updated = unchanged = unknown = 0
    for bar in bars:
        stock = stocks.get(bar.ticker)
        if stock is None:
            unknown += 1
            continue
        payload = {"ticker": bar.ticker, "date": bar.day.isoformat(), "open": bar.open, "high": bar.high,
                   "low": bar.low, "close": bar.close, "volume": bar.volume,
                   "turnover": bar.turnover, "trades": bar.trades}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        quote = existing_by_stock[stock.id].get(bar.day)
        if quote is not None and quote.content_hash == digest:
            unchanged += 1
            continue
        if quote is None:
            quote = StockQuote(stock_id=stock.id, timestamp=bar.last_at, data_mode="historical",
                               source=SOURCE, source_url=SOURCE_URL, source_identifier=file_digest,
                               source_timestamp=bar.last_at, fetched_at=datetime.now(KASE_TZ))
            session.add(quote)
            existing_by_stock[stock.id][bar.day] = quote
            created += 1
        else:
            updated += 1
        quote.timestamp = bar.last_at
        quote.open, quote.high, quote.low, quote.close, quote.last = bar.open, bar.high, bar.low, bar.close, bar.close
        quote.volume, quote.turnover, quote.number_of_trades = bar.volume, bar.turnover, bar.trades
        quote.data_mode, quote.content_hash = "historical", digest
        quote.source, quote.source_url, quote.source_identifier = SOURCE, SOURCE_URL, file_digest
        quote.source_timestamp, quote.fetched_at = bar.last_at, datetime.now(KASE_TZ)
    if dry_run:
        assert savepoint is not None
        savepoint.rollback()
        session.expire_all()
    else:
        session.commit()
    return {**metadata, "daily_bars": len(bars), "created": created, "updated": updated,
            "unchanged": unchanged, "unknown_tickers": unknown, "dry_run": dry_run}


__all__ = ["DailyBar", "import_deals_csv", "parse_deals_csv"]
