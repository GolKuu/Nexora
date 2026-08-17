"""Parser safety: deciding what is allowed to become history.

A page redesign turns prices into navigation labels, a locale change turns
``1 234,56`` into ``1``, a timezone bug moves trades to 1970. None of that may
be written into a permanent price series.

Every rejected record produces an :class:`~app.models.history.IngestionAnomaly`
with the raw payload attached, and the previously validated data is left exactly
as it was. Nothing here ever repairs a value - it only accepts or refuses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.services.backfill.records import (
    ObservationRecord,
    STATUS_TRADED,
    TradeRecord,
)

#: A price outside this range on KASE is a parse error, not a market move.
MIN_PRICE = 1e-6
MAX_PRICE = 1e9
#: The exchange opened in 1993; anything earlier is a broken timestamp.
EARLIEST_TIMESTAMP = datetime(1993, 1, 1, tzinfo=timezone.utc)
#: Clock skew tolerance for "in the future".
FUTURE_TOLERANCE = timedelta(hours=6)
#: A single-session move beyond this is treated as suspect until a second
#: source confirms it. KASE has daily limits; 10x in one day is a parser bug.
MAX_DAILY_MOVE = 5.0
#: If more than this share of a batch fails, the parser - not the market - is
#: broken, and the whole batch is rejected.
MAX_REJECT_RATIO = 0.5


@dataclass(slots=True)
class Rejection:
    kind: str
    message: str
    payload: dict


@dataclass(slots=True)
class ValidationOutcome:
    accepted: list
    rejections: list[Rejection]
    #: True when the batch as a whole looks like a parser failure.
    batch_rejected: bool = False

    @property
    def ok(self) -> bool:
        return not self.batch_rejected


def _payload(record) -> dict:
    return {
        key: (value.isoformat() if isinstance(value, datetime) else value)
        for key, value in vars(record).items()
        if value is not None
    }


def _check_timestamp(moment: datetime | None, now: datetime) -> str | None:
    if moment is None:
        return "нет отметки времени"
    if moment.tzinfo is None:
        return "отметка времени без часового пояса"
    if moment < EARLIEST_TIMESTAMP:
        return f"дата раньше начала торгов на KASE: {moment.isoformat()}"
    if moment > now + FUTURE_TOLERANCE:
        return f"дата в будущем: {moment.isoformat()}"
    return None


def _check_price(value: float | None, label: str) -> str | None:
    if value is None:
        return None
    if value != value:  # NaN
        return f"{label}: не число"
    if value <= 0:
        return f"{label}: неположительная цена {value}"
    if value < MIN_PRICE or value > MAX_PRICE:
        return f"{label}: цена вне допустимого диапазона ({value})"
    return None


def validate_observations(
    records: list[ObservationRecord],
    *,
    expected_ticker: str | None = None,
    reference_price: float | None = None,
    now: datetime | None = None,
) -> ValidationOutcome:
    """Filter a parsed batch down to what may be stored as fact."""
    now = now or datetime.now(timezone.utc)
    accepted: list[ObservationRecord] = []
    rejections: list[Rejection] = []

    for record in records:
        problem = _check_timestamp(record.observed_at, now)
        if problem:
            rejections.append(Rejection("broken_timestamp", problem, _payload(record)))
            continue

        price_problem = next(
            (
                message
                for value, label in (
                    (record.price, "price"), (record.open, "open"), (record.high, "high"),
                    (record.low, "low"), (record.close, "close"),
                    (record.bid, "bid"), (record.ask, "ask"),
                )
                if (message := _check_price(value, label))
            ),
            None,
        )
        if price_problem:
            rejections.append(Rejection("impossible_price", price_problem, _payload(record)))
            continue

        if record.high is not None and record.low is not None and record.high < record.low:
            rejections.append(
                Rejection("impossible_price", "high ниже low", _payload(record))
            )
            continue

        for label, value in (("volume", record.volume), ("turnover", record.turnover)):
            if value is not None and value < 0:
                rejections.append(
                    Rejection("impossible_price", f"{label}: отрицательное значение", _payload(record))
                )
                break
        else:
            if record.trade_count is not None and record.trade_count < 0:
                rejections.append(
                    Rejection("missing_fields", "отрицательное число сделок", _payload(record))
                )
                continue
            if record.status == STATUS_TRADED and not record.has_market_data:
                rejections.append(
                    Rejection("missing_fields", "статус 'traded' без единого значения", _payload(record))
                )
                continue
            observed = record.price if record.price is not None else record.close
            if reference_price and observed:
                ratio = observed / reference_price
                if ratio > MAX_DAILY_MOVE or ratio < 1.0 / MAX_DAILY_MOVE:
                    rejections.append(
                        Rejection(
                            "unrealistic_move",
                            f"цена изменилась в {ratio:.1f} раза относительно последней проверенной",
                            _payload(record),
                        )
                    )
                    continue
            accepted.append(record)

    batch_rejected = bool(records) and len(rejections) / len(records) > MAX_REJECT_RATIO
    if batch_rejected:
        # More than half the batch is nonsense: treat the parse as failed and
        # keep the previously validated history untouched.
        return ValidationOutcome(accepted=[], rejections=rejections, batch_rejected=True)
    return ValidationOutcome(accepted=accepted, rejections=rejections)


def validate_trades(
    records: list[TradeRecord], *, now: datetime | None = None
) -> ValidationOutcome:
    now = now or datetime.now(timezone.utc)
    accepted: list[TradeRecord] = []
    rejections: list[Rejection] = []
    for record in records:
        problem = _check_timestamp(record.trade_timestamp, now)
        if problem:
            rejections.append(Rejection("broken_timestamp", problem, _payload(record)))
            continue
        price_problem = _check_price(record.price, "price")
        if price_problem:
            rejections.append(Rejection("impossible_price", price_problem, _payload(record)))
            continue
        if record.quantity is not None and record.quantity <= 0:
            rejections.append(
                Rejection("impossible_price", "неположительный объем сделки", _payload(record))
            )
            continue
        accepted.append(record)

    batch_rejected = bool(records) and len(rejections) / len(records) > MAX_REJECT_RATIO
    if batch_rejected:
        return ValidationOutcome(accepted=[], rejections=rejections, batch_rejected=True)
    return ValidationOutcome(accepted=accepted, rejections=rejections)


def check_ticker(expected: str | None, seen: str | None) -> Rejection | None:
    """A page that answers about the wrong instrument is never stored."""
    if not expected or not seen:
        return None
    if expected.strip().upper() == seen.strip().upper():
        return None
    return Rejection(
        "wrong_ticker",
        f"страница вернула тикер {seen!r}, ожидался {expected!r}",
        {"expected": expected, "seen": seen},
    )


__all__ = [
    "MAX_DAILY_MOVE",
    "MAX_REJECT_RATIO",
    "Rejection",
    "ValidationOutcome",
    "check_ticker",
    "validate_observations",
    "validate_trades",
]
