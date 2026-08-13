"""How old is the data, and what should we therefore call it?

A quote fetched at yesterday's close is legitimately ``end_of_day``. The same
quote three weeks later is not - it is cached, and calling it anything else
tells the user the market said something today that it did not.

This module is the single place that decision is made, so the label the API
returns cannot drift away from the timestamp it is derived from.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.enums import DataMode

#: Past this age a live/delayed/end-of-day label decays to ``cached``.
#: Three days rather than one: KASE does not trade at weekends, so a Friday
#: close read on Monday morning is still the latest the market has said.
STALE_AFTER_SECONDS = 3 * 24 * 3600

#: Labels that describe a recent market observation and can therefore decay.
_PERISHABLE = {
    DataMode.LIVE.value,
    DataMode.DELAYED.value,
    DataMode.END_OF_DAY.value,
}


def age_seconds(timestamp: datetime | None, *, now: datetime | None = None) -> float | None:
    """Seconds since ``timestamp``. Naive values are read as UTC."""
    if timestamp is None:
        return None
    reference = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - timestamp).total_seconds()


def effective_data_mode(
    stored_mode: str | None,
    timestamp: datetime | None,
    *,
    now: datetime | None = None,
    stale_after: float = STALE_AFTER_SECONDS,
) -> str | None:
    """The label this data deserves *now*, not when it was fetched.

    ``mock`` is never rewritten: demo data stays flagged as demo no matter how
    fresh it looks. An unknown timestamp cannot be vouched for, so it decays
    too.
    """
    if stored_mode == DataMode.MOCK.value:
        return stored_mode
    if stored_mode not in _PERISHABLE:
        return stored_mode
    age = age_seconds(timestamp, now=now)
    if age is None or age > stale_after:
        return DataMode.CACHED.value
    return stored_mode


def freshness(
    stored_mode: str | None,
    timestamp: datetime | None,
    *,
    now: datetime | None = None,
    stale_after: float = STALE_AFTER_SECONDS,
) -> dict:
    """Everything the client needs to judge the data for itself."""
    age = age_seconds(timestamp, now=now)
    mode = effective_data_mode(
        stored_mode, timestamp, now=now, stale_after=stale_after
    )
    return {
        "data_mode": mode,
        "data_age_seconds": None if age is None else round(age, 1),
        "is_stale": bool(age is None or age > stale_after),
        "stale_after_seconds": stale_after,
    }
