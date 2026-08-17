"""Turning JSON payloads into validated facts.

The API accepts facts as plain JSON. Unknown keys are not silently swallowed -
they are collected and reported back, because a typo in ``net_debt_to_ebtida``
would otherwise present itself as "no leverage data" and quietly drag a score
toward the missing-data prior.
"""

from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime, timezone
from typing import Any, TypeVar, get_args, get_origin

from app.scoring.strict.facts import (
    BankFinancials,
    BondFacts,
    CreditEvents,
    DataMeta,
    IssuerFinancials,
    MacroFacts,
    MarketFacts,
    PeerFacts,
    Provenance,
    StockFacts,
)

T = TypeVar("T")


class FactsError(ValueError):
    """The payload cannot be turned into facts at all."""


_NESTED: dict[str, type] = {
    "market": MarketFacts,
    "financials": IssuerFinancials,
    "bank_financials": BankFinancials,
    "events": CreditEvents,
    "macro": MacroFacts,
    "peers": PeerFacts,
    "meta": DataMeta,
    "provenance": Provenance,
}


def _parse_datetime(value: Any, path: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise FactsError(f"{path}: не удалось разобрать дату {value!r}.") from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise FactsError(f"{path}: ожидалась дата, получено {type(value).__name__}.")


def _is_optional(annotation: Any, target: type) -> bool:
    args = get_args(annotation)
    return get_origin(annotation) is not None and any(
        arg is target or arg is type(None) for arg in args
    )


def _coerce(value: Any, annotation: Any, path: str) -> Any:
    if value is None:
        return None
    text = str(annotation)
    if "datetime" in text:
        return _parse_datetime(value, path)
    if "bool" in text and "float" not in text and "int" not in text:
        if isinstance(value, bool):
            return value
        raise FactsError(f"{path}: ожидалось true/false.")
    if "float" in text:
        if isinstance(value, bool):
            raise FactsError(f"{path}: ожидалось число, получено логическое значение.")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise FactsError(f"{path}: ожидалось число, получено {value!r}.") from exc
    if "int" in text:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise FactsError(f"{path}: ожидалось целое число, получено {value!r}.") from exc
    if "list" in text:
        if not isinstance(value, list):
            raise FactsError(f"{path}: ожидался список.")
        return list(value)
    return value


def _build(cls: type[T], payload: Any, unknown: list[str], path: str = "") -> T:
    if payload is None:
        return cls()
    if not isinstance(payload, dict):
        raise FactsError(f"{path or cls.__name__}: ожидался объект.")
    if not is_dataclass(cls):  # pragma: no cover - guarded by _NESTED
        raise FactsError(f"{path}: неподдерживаемый тип.")

    known = {f.name: f for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in payload.items():
        where = f"{path}.{key}" if path else key
        field = known.get(key)
        if field is None:
            unknown.append(where)
            continue
        nested = _NESTED.get(key)
        if nested is not None and key != "provenance":
            kwargs[key] = _build(nested, value, unknown, where)
        elif key == "provenance":
            kwargs[key] = _build(Provenance, value, unknown, where)
        else:
            kwargs[key] = _coerce(value, field.type, where)

    # Nested blocks the caller omitted keep their dataclass defaults.
    for name, field in known.items():
        if name in kwargs:
            continue
        if field.default is MISSING and field.default_factory is MISSING:  # type: ignore[misc]
            raise FactsError(f"{path or cls.__name__}: не хватает поля {name}.")
    return cls(**kwargs)


def bond_facts_from_dict(payload: dict) -> tuple[BondFacts, list[str]]:
    unknown: list[str] = []
    return _build(BondFacts, payload, unknown), unknown


def stock_facts_from_dict(payload: dict) -> tuple[StockFacts, list[str]]:
    unknown: list[str] = []
    return _build(StockFacts, payload, unknown), unknown
