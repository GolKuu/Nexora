"""Turning public KASE tables into historical records.

Pure functions over already-extracted table data: no browser, no database, no
network. That is what makes the risky part of the backfill - reading somebody
else's HTML - unit-testable against fixtures instead of only against the live
site.

Column names are *discovered* from the header row in Russian, Kazakh or English.
A column we do not recognise is left alone; a value we cannot parse becomes
``None`` and never a zero.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timezone

from app.browser.normalize import is_empty, parse_date, parse_number
from app.forecast.calendar import KASE_TZ
from app.services.backfill.records import (
    DividendRecord,
    ObservationRecord,
    STATUS_NO_TRADE,
    STATUS_TRADED,
    TradeRecord,
)

PARSER_VERSION = "kase-history-parser-v1"

#: header vocabulary -> canonical field
COLUMN_VOCABULARY: dict[str, tuple[str, ...]] = {
    "date": ("дата", "күн", "date", "day", "торговый день", "trade date"),
    "time": ("время", "уақыт", "time"),
    "price": ("цена", "баға", "price", "последняя", "last", "цена сделки", "средневзвеш"),
    "open": ("открытие", "open", "цена открытия", "ашылу"),
    "high": ("максимум", "макс", "high", "наибольшая"),
    "low": ("минимум", "мин", "low", "наименьшая"),
    "close": ("закрытие", "close", "цена закрытия", "жабылу"),
    "previous_close": ("пред. закрытие", "предыдущее закрытие", "previous close", "prev close"),
    "volume": ("объем", "объём", "количество", "quantity", "volume", "көлем", "штук"),
    "turnover": ("оборот", "сумма", "turnover", "value", "объем, тенге", "айналым"),
    "trade_count": ("сделок", "кол-во сделок", "число сделок", "trades", "deals", "мәміле"),
    "bid": ("спрос", "покупка", "bid", "лучший спрос", "сұраныс"),
    "ask": ("предложение", "продажа", "ask", "offer", "лучшее предложение", "ұсыныс"),
    "bid_volume": ("объем спроса", "bid size", "bid volume"),
    "ask_volume": ("объем предложения", "ask size", "ask volume"),
    "trade_id": ("номер сделки", "id сделки", "trade id", "№ сделки", "код сделки"),
    "amount_per_share": ("дивиденд", "размер дивиденда", "на одну акцию", "dividend", "per share"),
    "ex_date": ("экс-дивидендная", "ex-date", "ex date", "отсечка"),
    "record_date": ("дата фиксации", "record date", "дата отсечки реестра", "реестр"),
    "payment_date": ("дата выплаты", "payment date", "выплата"),
    "announcement_date": ("дата объявления", "announcement", "дата решения"),
    "currency": ("валюта", "currency", "валюта расчета"),
}

_WS = re.compile(r"\s+")
_TIME = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def _fold(value: str | None) -> str:
    return _WS.sub(" ", (value or "")).strip().casefold()


def classify_column(header: str | None) -> str | None:
    """Map one header cell onto a canonical field name.

    Longer vocabulary entries win, so "цена закрытия" is a close and not a
    generic price.
    """
    folded = _fold(header)
    if not folded:
        return None
    best: tuple[int, str] | None = None
    for field, words in COLUMN_VOCABULARY.items():
        for word in words:
            if word in folded and (best is None or len(word) > best[0]):
                best = (len(word), field)
    return None if best is None else best[1]


def map_headers(headers: list[str]) -> dict[str, int]:
    """Column index for every field we recognised. First match wins."""
    mapping: dict[str, int] = {}
    for index, header in enumerate(headers):
        field = classify_column(header)
        if field and field not in mapping:
            mapping[field] = index
    return mapping


def _cell(row: list[str], mapping: dict[str, int], field: str) -> str | None:
    index = mapping.get(field)
    if index is None or index >= len(row):
        return None
    value = row[index]
    return None if is_empty(value) else value


def _number(row: list[str], mapping: dict[str, int], field: str) -> float | None:
    return parse_number(_cell(row, mapping, field))


def _timestamp(row: list[str], mapping: dict[str, int]) -> datetime | None:
    """Combine the date column with the time column when both exist.

    A row with a date but no time is timestamped at the KASE close, not at
    midnight UTC - which would silently move it to the previous trading day.
    """
    day = parse_date(_cell(row, mapping, "date"))
    if day is None:
        return None
    raw_time = _cell(row, mapping, "time")
    match = _TIME.search(raw_time or "")
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        second = int(match.group(3) or 0)
        if hour > 23 or minute > 59 or second > 59:
            return None
        clock = time(hour, minute, second)
    else:
        clock = time(17, 0)
    return datetime.combine(day, clock, tzinfo=KASE_TZ).astimezone(timezone.utc)


def parse_price_history(
    headers: list[str],
    rows: list[list[str]],
    *,
    source: str | None = None,
    source_url: str | None = None,
    data_mode: str = "browser",
) -> list[ObservationRecord]:
    """One observation per table row.

    OHLC fields appear only when the table itself has those columns. A table
    with a single price column produces a single price - the other three are
    left NULL rather than filled with a copy of it.
    """
    mapping = map_headers(headers)
    if "date" not in mapping:
        return []
    records: list[ObservationRecord] = []
    for row in rows:
        observed_at = _timestamp(row, mapping)
        if observed_at is None:
            continue
        price = _number(row, mapping, "price")
        close = _number(row, mapping, "close")
        volume = _number(row, mapping, "volume")
        turnover = _number(row, mapping, "turnover")
        trade_count = _number(row, mapping, "trade_count")
        bid = _number(row, mapping, "bid")
        ask = _number(row, mapping, "ask")

        has_trade = any(v is not None for v in (price, close, volume, turnover))
        if not has_trade and bid is None and ask is None:
            # The row exists, so the day is known - but nothing traded on it.
            # It is stored as an explicit no-trade marker, never as a repeat of
            # the previous close.
            records.append(
                ObservationRecord(
                    observed_at=observed_at, status=STATUS_NO_TRADE, source=source,
                    source_url=source_url, parser_version=PARSER_VERSION,
                    data_mode=data_mode, trading_date=observed_at.astimezone(KASE_TZ).date(),
                )
            )
            continue

        records.append(
            ObservationRecord(
                observed_at=observed_at,
                price=price if price is not None else close,
                open=_number(row, mapping, "open"),
                high=_number(row, mapping, "high"),
                low=_number(row, mapping, "low"),
                close=close,
                previous_close=_number(row, mapping, "previous_close"),
                bid=bid,
                ask=ask,
                bid_volume=_number(row, mapping, "bid_volume"),
                ask_volume=_number(row, mapping, "ask_volume"),
                volume=volume,
                turnover=turnover,
                trade_count=None if trade_count is None else int(trade_count),
                status=STATUS_TRADED if has_trade else STATUS_NO_TRADE,
                source=source,
                source_url=source_url,
                source_timestamp=observed_at,
                parser_version=PARSER_VERSION,
                data_mode=data_mode,
                trading_date=observed_at.astimezone(KASE_TZ).date(),
            )
        )
    return records


def parse_trades(
    headers: list[str],
    rows: list[list[str]],
    *,
    source: str | None = None,
    source_url: str | None = None,
    data_mode: str = "browser",
) -> list[TradeRecord]:
    """Individual deals, where KASE publishes them."""
    mapping = map_headers(headers)
    if "date" not in mapping or "price" not in mapping:
        return []
    records: list[TradeRecord] = []
    for row in rows:
        timestamp = _timestamp(row, mapping)
        price = _number(row, mapping, "price")
        if timestamp is None or price is None:
            continue
        quantity = _number(row, mapping, "volume")
        value = _number(row, mapping, "turnover")
        trade_id = _cell(row, mapping, "trade_id")
        records.append(
            TradeRecord(
                trade_timestamp=timestamp,
                price=price,
                quantity=quantity,
                trade_value=value,
                trade_id=trade_id.strip() if trade_id else None,
                source=source,
                source_url=source_url,
                source_timestamp=timestamp,
                parser_version=PARSER_VERSION,
                data_mode=data_mode,
            )
        )
    return records


def parse_dividends(
    headers: list[str],
    rows: list[list[str]],
    *,
    source: str | None = None,
    source_url: str | None = None,
) -> list[DividendRecord]:
    """Published dividend decisions. Future payments are never extrapolated."""
    mapping = map_headers(headers)
    amount_index = mapping.get("amount_per_share")
    if amount_index is None:
        return []
    records: list[DividendRecord] = []
    for row in rows:
        amount = _number(row, mapping, "amount_per_share")
        if amount is None:
            continue
        currency = (_cell(row, mapping, "currency") or "KZT").strip().upper()[:3] or "KZT"
        records.append(
            DividendRecord(
                amount_per_share=amount,
                currency=currency,
                announcement_date=parse_date(_cell(row, mapping, "announcement_date")),
                ex_date=parse_date(_cell(row, mapping, "ex_date")),
                record_date=parse_date(_cell(row, mapping, "record_date")),
                payment_date=parse_date(_cell(row, mapping, "payment_date"))
                or parse_date(_cell(row, mapping, "date")),
                source=source,
                source_url=source_url,
                parser_version=PARSER_VERSION,
            )
        )
    return records


def looks_like_price_table(headers: list[str]) -> bool:
    mapping = map_headers(headers)
    return "date" in mapping and bool(
        {"price", "close", "open", "volume", "turnover"} & set(mapping)
    )


def looks_like_trade_table(headers: list[str]) -> bool:
    mapping = map_headers(headers)
    return "date" in mapping and "price" in mapping and (
        "trade_id" in mapping or "time" in mapping
    )


def looks_like_dividend_table(headers: list[str]) -> bool:
    return "amount_per_share" in map_headers(headers)


__all__ = [
    "COLUMN_VOCABULARY",
    "PARSER_VERSION",
    "classify_column",
    "looks_like_dividend_table",
    "looks_like_price_table",
    "looks_like_trade_table",
    "map_headers",
    "parse_dividends",
    "parse_price_history",
    "parse_trades",
]
