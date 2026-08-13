"""Turning what the site *shows* into what the system *stores* (§44, §45, §46).

Three rules, and the first one outranks the other two:

1. the raw string is never lost - ``ExtractedValue.raw`` always keeps exactly
   what KASE printed;
2. a percentage becomes a decimal fraction (``"18,45 %"`` -> ``0.1845``),
   because that is the internal representation the calculation engine uses;
3. a value that cannot be parsed confidently comes back as ``None`` with a
   warning attached. Guessing a number is worse than not having one.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

#: Everything that means "no value" on kase.kz.
EMPTY_TOKENS = {"", "-", "–", "—", "н/д", "нет данных", "n/a", "na", "null", "не указано"}

#: Currency spellings -> ISO code.
CURRENCY_ALIASES = {
    "₸": "KZT", "тенге": "KZT", "тг": "KZT", "kzt": "KZT",
    "$": "USD", "usd": "USD", "доллар": "USD", "долларов сша": "USD",
    "€": "EUR", "eur": "EUR", "евро": "EUR",
    "₽": "RUB", "rub": "RUB", "рубл": "RUB",
    "¥": "CNY", "cny": "CNY", "юан": "CNY",
}

#: A run of digits that may contain group separators (space, non-breaking
#: space, apostrophe) and a decimal separator. Anchored on a digit at both
#: ends so a trailing "%" or a dangling comma never lands inside the token.
_NUMBER = re.compile(r"[-+]?\d[\d\s  '.,]*\d|[-+]?\d")
_PERCENT_HINT = re.compile(r"%|проц|percent", re.IGNORECASE)

#: Formats KASE actually uses, most specific first.
_DATE_FORMATS = (
    "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y",
    "%d.%m.%Y %H:%M", "%d.%m.%y %H:%M", "%d.%m.%y (%H:%M)",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%y %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
)

_MONTHS_RU = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


def is_empty(raw: str | None) -> bool:
    return raw is None or raw.strip().casefold() in EMPTY_TOKENS


def parse_number(raw: str | None) -> float | None:
    """``"1 234,56"`` -> ``1234.56``. Locale-tolerant, guess-free.

    Handles the thin/non-breaking spaces KASE uses as thousand separators and
    both decimal conventions. Returns ``None`` when the string holds no number.
    """
    if is_empty(raw):
        return None
    match = _NUMBER.search(raw)  # type: ignore[arg-type]
    if match is None:
        return None
    token = re.sub(r"[\s  ']", "", match.group(0)).strip(".,")
    if "," in token and "." in token:
        # Whichever separator comes last is the decimal one.
        token = (
            token.replace(",", "")
            if token.rfind(".") > token.rfind(",")
            else token.replace(".", "").replace(",", ".")
        )
    else:
        for separator in (",", "."):
            if token.count(separator) > 1:
                # Repeated: it can only be a thousands separator.
                token = token.replace(separator, "")
        token = token.replace(",", ".")
    try:
        return float(token)
    except ValueError:
        return None


def parse_percent(raw: str | None) -> float | None:
    """``"18,45 %"`` -> ``0.1845``.

    A bare number is still treated as a percentage here, because this is only
    called for fields the page labels as a rate. Callers that are unsure should
    use :func:`parse_number` and decide themselves.
    """
    value = parse_number(raw)
    return None if value is None else value / 100.0


def looks_like_percent(raw: str | None, label: str | None = None) -> bool:
    return bool(
        (raw and _PERCENT_HINT.search(raw))
        or (label and _PERCENT_HINT.search(label))
    )


def parse_date(raw: str | None) -> date | None:
    """Parse the date formats the site uses; return ``None`` on anything else.

    Two-digit years are resolved by ``%y``, which maps 00-68 to the 2000s -
    correct for every instrument KASE lists.
    """
    if is_empty(raw):
        return None
    text = raw.strip()  # type: ignore[union-attr]
    text = re.sub(r"\s+", " ", text)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # "12 августа 2026"
    match = re.match(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", text, re.IGNORECASE)
    if match:
        day, month_word, year = match.groups()
        for prefix, month in _MONTHS_RU.items():
            if month_word.lower().startswith(prefix):
                try:
                    return date(int(year), month, int(day))
                except ValueError:
                    return None
    # A date embedded in a longer string, e.g. "с 11.07.24 по 11.07.27".
    embedded = re.search(r"\d{2}\.\d{2}\.\d{2,4}", text)
    if embedded:
        return parse_date(embedded.group(0))
    return None


def parse_datetime(raw: str | None, *, assume_tz=timezone.utc) -> datetime | None:
    """Parse a timestamp and always return it timezone-aware (§45).

    KASE prints Almaty local time without an offset. We do not silently claim
    UTC for such a value: the caller passes the timezone it knows applies.
    """
    if is_empty(raw):
        return None
    text = re.sub(r"\s+", " ", raw.strip())  # type: ignore[union-attr]
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=parsed.tzinfo or assume_tz)
    parsed_date = parse_date(text)
    if parsed_date is None:
        return None
    return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=assume_tz)


def parse_currency(raw: str | None) -> str | None:
    """``"млн ₸"`` / ``"тенге"`` / ``"KZT"`` -> ``"KZT"``."""
    if is_empty(raw):
        return None
    folded = raw.strip().casefold()  # type: ignore[union-attr]
    for alias, code in CURRENCY_ALIASES.items():
        if alias in folded:
            return code
    match = re.search(r"\b([A-Z]{3})\b", raw)  # type: ignore[arg-type]
    return match.group(1) if match else None


def parse_money(raw: str | None, *, default_currency: str | None = None) -> tuple[float | None, str | None]:
    """``"3 000 000 000 KZT"`` -> ``(3e9, "KZT")``, scale words applied."""
    amount = parse_number(raw)
    currency = parse_currency(raw) or default_currency
    if amount is not None and raw:
        folded = raw.casefold()
        if "млрд" in folded or "billion" in folded:
            amount *= 1_000_000_000
        elif "млн" in folded or "million" in folded or "m." in folded:
            amount *= 1_000_000
        elif "тыс" in folded or "th." in folded or "thousand" in folded:
            amount *= 1_000
    return amount, currency


def normalize_ticker(raw: str | None) -> str | None:
    if is_empty(raw):
        return None
    token = re.sub(r"\s+", "", raw)  # type: ignore[arg-type]
    return token if re.fullmatch(r"[A-Za-z0-9_.\-]{2,32}", token) else None


_ISIN = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")


def normalize_isin(raw: str | None) -> str | None:
    """Extract an ISIN and check its check digit. Invalid -> ``None``.

    Spaces are deliberately *not* stripped before matching. Doing so glues the
    code to whatever word precedes it ("облигации KZ2C00004273" becomes
    "облигацииKZ2C00004273"), the leading ``\\b`` stops matching, and the
    search silently walks on to the next ISIN on the page - which on a KASE
    instrument page belongs to a different bond in the related-securities
    list.
    """
    if is_empty(raw):
        return None
    match = _ISIN.search(raw.upper())  # type: ignore[union-attr]
    if match is None:
        return None
    isin = match.group(1)
    return isin if _isin_check_digit_ok(isin) else None


def find_isins(raw: str | None) -> list[str]:
    """Every distinct, checksum-valid ISIN in ``raw``, in order of appearance."""
    if is_empty(raw):
        return []
    seen: list[str] = []
    for match in _ISIN.finditer(raw.upper()):  # type: ignore[union-attr]
        isin = match.group(1)
        if _isin_check_digit_ok(isin) and isin not in seen:
            seen.append(isin)
    return seen


def _isin_check_digit_ok(isin: str) -> bool:
    digits = "".join(
        str(int(char, 36)) if char.isalpha() else char for char in isin[:-1]
    )
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return (10 - total % 10) % 10 == int(isin[-1])
