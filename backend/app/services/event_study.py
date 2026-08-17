"""Factual event/price alignment. This module never invokes AI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import pstdev
from typing import Iterable


@dataclass(frozen=True)
class QuotePoint:
    timestamp: datetime
    price: float
    volume: float | None = None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _ret(p0: float | None, p1: float | None) -> float | None:
    return None if p0 in (None, 0) or p1 is None else p1 / p0 - 1.0


def align_event_to_quotes(event_timestamp: datetime, quotes: Iterable[QuotePoint]) -> dict:
    """Align intraday windows and trading-session horizons without calendar-day assumptions."""
    event_ts = _aware(event_timestamp)
    points = sorted((QuotePoint(_aware(q.timestamp), q.price, q.volume) for q in quotes if q.price is not None), key=lambda q: q.timestamp)
    before = [q for q in points if q.timestamp <= event_ts]
    p0_point = before[-1] if before else None
    future = [q for q in points if q.timestamp > event_ts]
    # If the event is outside market hours, the first future quote starts the
    # reaction session; p0 remains the last genuinely available prior price.
    has_same_day_future = any(q.timestamp.date() == event_ts.date() for q in future)
    effective = event_ts if has_same_day_future else (future[0].timestamp if future else event_ts)

    def after_delta(delta: timedelta) -> QuotePoint | None:
        target = effective + delta
        return next((q for q in future if q.timestamp >= target), None)

    # Last quote per observed trading date. Missing weekends/holidays vanish.
    sessions: list[QuotePoint] = []
    for q in points:
        if not sessions or sessions[-1].timestamp.date() != q.timestamp.date(): sessions.append(q)
        else: sessions[-1] = q
    effective_date = effective.date()
    session_index = next((i for i, q in enumerate(sessions) if q.timestamp.date() >= effective_date), None)
    same = sessions[session_index] if session_index is not None else None
    def session_after(offset: int) -> QuotePoint | None:
        if session_index is None or session_index + offset >= len(sessions): return None
        return sessions[session_index + offset]
    def point_price(point: QuotePoint | None) -> float | None:
        return point.price if point is not None else None

    baseline = [q.volume for q in before[-20:] if q.volume is not None and q.volume > 0]
    event_volume = same.volume if same else None
    volume_ratio = (event_volume / (sum(baseline) / len(baseline))) if event_volume is not None and baseline else None
    pre_prices = [q.price for q in before[-21:]]
    post_prices = [q.price for q in points if q.timestamp >= effective][:21]
    def volatility(values: list[float]) -> float | None:
        returns = [values[i] / values[i-1] - 1 for i in range(1, len(values)) if values[i-1]]
        return pstdev(returns) if len(returns) > 1 else None
    pre_vol, post_vol = volatility(pre_prices), volatility(post_prices)
    p0 = p0_point.price if p0_point else None
    closed_session_shift = 0 if effective_date > event_ts.date() else 1
    return {
        "price_before": p0,
        "return_5m": _ret(p0, point_price(after_delta(timedelta(minutes=5)))),
        "return_30m": _ret(p0, point_price(after_delta(timedelta(minutes=30)))),
        "return_1h": _ret(p0, point_price(after_delta(timedelta(hours=1)))),
        "return_same_day": _ret(p0, same.price if same else None),
        "return_1d": _ret(p0, point_price(session_after(closed_session_shift))),
        "return_5d": _ret(p0, point_price(session_after(closed_session_shift + 4))),
        "return_20d": _ret(p0, point_price(session_after(closed_session_shift + 19))),
        "volume_ratio": volume_ratio,
        "volatility_change": (post_vol - pre_vol) if pre_vol is not None and post_vol is not None else None,
        "effective_session": effective,
    }


def abnormal_return(stock_return: float | None, benchmark_return: float | None) -> float | None:
    return stock_return - benchmark_return if stock_return is not None and benchmark_return is not None else None


__all__ = ["QuotePoint", "abnormal_return", "align_event_to_quotes"]
