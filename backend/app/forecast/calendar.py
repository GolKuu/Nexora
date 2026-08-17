"""Shared KASE trading-session calendar for labels, paths and evaluation."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

KASE_TZ = timezone(timedelta(hours=5))


def kase_holidays(year: int) -> set[date]:
    """National KASE closures plus weekday substitution for weekend holidays."""
    fixed = {(1, 1), (1, 2), (1, 7), (3, 8), (3, 21), (3, 22), (3, 23),
             (5, 1), (5, 7), (5, 9), (7, 6), (8, 30), (10, 25), (12, 16)}
    holidays = {date(year, month, day) for month, day in fixed}
    occupied = set(holidays)
    for holiday in sorted(holidays):
        if holiday.weekday() >= 5:
            substitute = holiday + timedelta(days=1)
            while substitute.weekday() >= 5 or substitute in occupied:
                substitute += timedelta(days=1)
            occupied.add(substitute)
    return occupied


def kase_date(timestamp: datetime) -> date:
    return timestamp.astimezone(KASE_TZ).date() if timestamp.tzinfo else timestamp.date()


def trading_days(start: datetime, count: int) -> list[datetime]:
    days: list[datetime] = []
    cursor = start
    while len(days) < count:
        cursor += timedelta(days=1)
        local_date = kase_date(cursor)
        if local_date.weekday() < 5 and local_date not in kase_holidays(local_date.year):
            days.append(cursor)
    return days


def previous_trading_days(start: datetime, count: int) -> list[datetime]:
    days: list[datetime] = []
    cursor = start
    while len(days) < count:
        cursor -= timedelta(days=1)
        local_date = kase_date(cursor)
        if local_date.weekday() < 5 and local_date not in kase_holidays(local_date.year):
            days.append(cursor)
    return days


__all__ = ["KASE_TZ", "kase_date", "kase_holidays", "previous_trading_days", "trading_days"]
