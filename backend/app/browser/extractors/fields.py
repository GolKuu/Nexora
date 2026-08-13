"""Mapping KASE's own labels onto our field names.

This is a *dictionary*, not a parser: the label text KASE prints is matched
against known spellings, and unknown labels are kept as-is rather than dropped,
so a renamed or added parameter shows up in the raw payload even before this
table learns about it.

Every produced value carries its raw string, so ``"18,45 %"`` and ``0.1845``
are both preserved (§44).
"""

from __future__ import annotations

import re
from typing import Callable

from app.browser.normalize import (
    parse_date,
    parse_money,
    parse_number,
    parse_percent,
    normalize_isin,
    normalize_ticker,
)
from app.browser.types import ExtractedValue, ExtractionMethod, SourceRef
from app.browser.validator import make_value

#: Coupon-type words -> our CouponType values.
COUPON_TYPES = {
    "фиксирован": "fixed",
    "fixed": "fixed",
    "плавающ": "floating",
    "float": "floating",
    "индексирован": "indexed",
    "indexed": "indexed",
    "дисконт": "zero",
    "нулев": "zero",
    "zero": "zero",
    "ступенч": "step",
    "step": "step",
}


def _coupon_type(raw: str) -> str | None:
    folded = raw.casefold()
    for word, value in COUPON_TYPES.items():
        if word in folded:
            return value
    return None


def _first_date(raw: str):
    """First date out of a range like ``"11.07.27–23.07.27"``."""
    match = re.search(r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}", raw)
    return parse_date(match.group(0)) if match else parse_date(raw)


def _last_date(raw: str):
    matches = re.findall(r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}", raw)
    return parse_date(matches[-1]) if matches else parse_date(raw)


def _money(raw: str):
    amount, _currency = parse_money(raw)
    return amount


#: label fragment (casefolded, matched by containment) -> (field, parser, unit)
LABEL_FIELDS: list[tuple[str, str, Callable[[str], object], str | None]] = [
    ("наименование облигации", "name", lambda raw: raw.strip(), None),
    ("валюта выпуска", "currency", lambda raw: raw.strip().upper()[:8], None),
    ("валюта котирования", "quote_currency", lambda raw: raw.strip().upper()[:8], None),
    ("номинальная стоимость", "nominal", _money, "currency"),
    ("число зарегистрированных облигаций", "registered_count", parse_number, "units"),
    ("число облигаций в обращении", "outstanding_count", parse_number, "units"),
    ("issue volume", "issue_size", _money, "currency"),
    ("объем выпуска", "issue_size", _money, "currency"),
    ("isin", "isin", normalize_isin, None),
    ("вид купонной ставки", "coupon_type", _coupon_type, None),
    ("текущая купонная ставка", "coupon_rate", parse_percent, "fraction"),
    ("последняя купонная ставка", "coupon_rate", parse_percent, "fraction"),
    ("ставка купона", "coupon_rate", parse_percent, "fraction"),
    ("расчетный базис", "day_count", lambda raw: raw.strip(), None),
    ("дата начала обращения", "issue_date", _first_date, "date"),
    ("срок обращения, лет", "term_years", parse_number, "years"),
    ("срок обращения, дней", "term_days", parse_number, "days"),
    ("период погашения", "maturity_date", _first_date, "date"),
    ("дата погашения", "maturity_date", _first_date, "date"),
    ("количество дней до погашения", "days_to_maturity", parse_number, "days"),
    ("код бумаги", "ticker", normalize_ticker, None),
    ("торговый код", "ticker", normalize_ticker, None),
    ("период ближайшей купонной выплаты", "next_coupon_date", _first_date, "date"),
    ("дата ближайшей купонной выплаты", "next_coupon_date", _first_date, "date"),
    ("дата предыдущей купонной выплаты", "previous_coupon_date", _first_date, "date"),
    ("число дней до ближайшей купонной выплаты", "days_to_next_coupon", parse_number, "days"),
    ("период обращения", "issue_date", _first_date, "date"),
    ("маркет-мейкер", "market_maker", lambda raw: raw.strip(), None),
    ("список ценных бумаг", "listing", lambda raw: raw.strip(), None),
    ("площадка", "market_segment", lambda raw: raw.strip(), None),
    ("сектор", "sector", lambda raw: raw.strip(), None),
    ("категория", "category", lambda raw: raw.strip(), None),
    ("esg облигации", "esg", lambda raw: raw.strip(), None),
    ("инициатор допуска", "issuer_name_raw", lambda raw: raw.strip(), None),
    ("предмет котирования", "quote_subject", lambda raw: raw.strip(), None),
]

#: Labels whose value is a second appearance of something already captured -
#: kept for cross-checking (§30) rather than overwriting.
CROSS_CHECK_LABELS = {"isin", "ticker", "coupon_rate", "maturity_date"}


def field_for_label(label: str) -> tuple[str, Callable[[str], object], str | None] | None:
    folded = re.sub(r"\s+", " ", label).strip().casefold()
    for fragment, name, parser, unit in LABEL_FIELDS:
        if fragment in folded:
            return name, parser, unit
    return None


def values_from_labels(
    pairs: dict[str, str],
    *,
    source: SourceRef | None = None,
    method: str = ExtractionMethod.TABLE.value,
) -> tuple[list[ExtractedValue], dict[str, str]]:
    """Convert ``{label: value}`` into typed candidates plus the leftovers.

    The second element is every label this table did not recognise. It is not
    thrown away - it is stored with the snapshot so a new KASE parameter is
    visible immediately, even before it gets a mapping here.
    """
    values: list[ExtractedValue] = []
    unmapped: dict[str, str] = {}
    for label, raw in pairs.items():
        mapping = field_for_label(label)
        if mapping is None:
            unmapped[label] = raw
            continue
        name, parser, unit = mapping
        try:
            normalized = parser(raw)
        except Exception:
            normalized = None
        if normalized is None:
            unmapped[label] = raw
            continue
        values.append(
            make_value(
                name,
                raw,
                normalized,
                method=method,
                source=source,
                label=label,
                unit=unit,
            )
        )
    return values, unmapped


#: Catalogue column header -> field. Same containment matching.
CATALOG_COLUMNS: list[tuple[str, str]] = [
    ("код", "ticker"),
    ("компания", "issuer_name"),
    ("эмитент", "issuer_name"),
    ("isin", "isin"),
    ("валюта", "currency"),
    ("доходность", "ytm"),
    ("купон", "coupon"),
    ("погашение", "maturity"),
    ("объем", "volume"),
    ("дата", "as_of"),
    ("маркет-мейкер", "market_maker"),
]


def catalog_field_for(header: str) -> str | None:
    folded = re.sub(r"\s+", " ", header).strip().casefold()
    for fragment, name in CATALOG_COLUMNS:
        if fragment in folded:
            return name
    return None
