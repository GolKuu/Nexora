"""Day-count conventions and calendar helpers.

Implemented locally (no third-party date library) so the numeric core has no
dependencies at all.
"""

from __future__ import annotations

import calendar
from datetime import date

SUPPORTED = ("ACT/365F", "ACT/360", "30/360", "ACT/ACT")


def add_months(anchor: date, months: int) -> date:
    """Add whole months, clamping to the last valid day of the target month."""
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _is_leap(year: int) -> bool:
    return calendar.isleap(year)


def year_fraction(start: date, end: date, convention: str = "ACT/365F") -> float:
    """Year fraction between two dates. Negative if ``end`` precedes ``start``."""
    if start == end:
        return 0.0
    if end < start:
        return -year_fraction(end, start, convention)

    conv = convention.upper()
    if conv == "ACT/365F":
        return (end - start).days / 365.0
    if conv == "ACT/360":
        return (end - start).days / 360.0
    if conv == "30/360":
        d1 = min(start.day, 30)
        d2 = min(end.day, 30) if d1 == 30 else end.day
        return (
            360 * (end.year - start.year)
            + 30 * (end.month - start.month)
            + (d2 - d1)
        ) / 360.0
    if conv == "ACT/ACT":
        # ISDA: split the interval per calendar year.
        total = 0.0
        for year in range(start.year, end.year + 1):
            period_start = max(start, date(year, 1, 1))
            period_end = min(end, date(year + 1, 1, 1))
            if period_end <= period_start:
                continue
            total += (period_end - period_start).days / (366.0 if _is_leap(year) else 365.0)
        return total
    raise ValueError(f"Unsupported day count convention: {convention}")


def months_per_period(frequency: int) -> int:
    if frequency <= 0 or 12 % frequency != 0:
        raise ValueError(f"Unsupported coupon frequency: {frequency}")
    return 12 // frequency
