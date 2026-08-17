"""The historical window, computed from the moment the job runs.

No calendar date is ever hard-coded. ``backfill_start`` is always
``execution_date - HISTORICAL_BACKFILL_YEARS``, so a run in 2027 asks for
2025-2027 without anybody editing a constant.

The window is only what we *request*. What KASE actually exposes is measured
separately (see :mod:`app.services.backfill.coverage`) and reported honestly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.core.config import settings
from app.forecast.calendar import KASE_TZ, kase_date, kase_holidays


def shift_years(moment: date, years: int) -> date:
    """Subtract whole years, surviving 29 February.

    ``date(2024, 2, 29)`` two years back has no counterpart in 2022; the
    convention here is 28 February, the last day that exists in that month.
    """
    try:
        return moment.replace(year=moment.year - years)
    except ValueError:
        # 29 February in a non-leap target year.
        return moment.replace(year=moment.year - years, month=2, day=28)


@dataclass(frozen=True, slots=True)
class BackfillWindow:
    """What this run asks KASE for."""

    start: datetime
    end: datetime
    years: int

    @property
    def start_date(self) -> date:
        return kase_date(self.start)

    @property
    def end_date(self) -> date:
        return kase_date(self.end)

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days

    def contains(self, moment: datetime | date | None) -> bool:
        if moment is None:
            return False
        day = moment if isinstance(moment, date) and not isinstance(moment, datetime) else kase_date(moment)
        return self.start_date <= day <= self.end_date

    def to_dict(self) -> dict:
        return {
            "requested_start": self.start.isoformat(),
            "requested_end": self.end.isoformat(),
            "years": self.years,
            "days": self.days,
            "market_days_expected": expected_market_days(self.start_date, self.end_date),
        }


def backfill_window(
    *, now: datetime | None = None, years: int | None = None
) -> BackfillWindow:
    """The rolling window for this execution.

    ``now`` is injectable so the tests can pin an execution date; production
    always passes the real clock.
    """
    years = settings.HISTORICAL_BACKFILL_YEARS if years is None else years
    moment = (now or datetime.now(timezone.utc)).astimezone(KASE_TZ)
    start_day = shift_years(moment.date(), years)
    start = datetime.combine(start_day, moment.timetz()).astimezone(timezone.utc)
    return BackfillWindow(start=start, end=moment.astimezone(timezone.utc), years=years)


def market_days(start: date, end: date) -> list[date]:
    """Every KASE trading day in ``[start, end]``.

    Weekends and national holidays are excluded, which is why a chart with no
    point on 1 January is complete rather than missing data.
    """
    if end < start:
        return []
    holidays: dict[int, set[date]] = {}
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            year_holidays = holidays.get(cursor.year)
            if year_holidays is None:
                year_holidays = holidays[cursor.year] = kase_holidays(cursor.year)
            if cursor not in year_holidays:
                days.append(cursor)
        cursor += timedelta(days=1)
    return days


def expected_market_days(start: date, end: date) -> int:
    return len(market_days(start, end))


def is_market_day(day: date) -> bool:
    return day.weekday() < 5 and day not in kase_holidays(day.year)


__all__ = [
    "BackfillWindow",
    "backfill_window",
    "expected_market_days",
    "is_market_day",
    "market_days",
    "shift_years",
]
