"""Cleaning pipeline (§9).

The failure this module exists to prevent: training a financial model on site
navigation, cookie banners and footer boilerplate, then wondering why it
answers questions about bonds with "Личный кабинет | Контакты | © KASE".

Every function is pure and independently testable. ``clean_document`` runs them
in order and returns the text plus a quality score; ``quality_score`` below
0.35 is dropped by the builder and the drop is counted in the manifest.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Iterable

# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript|svg|iframe)\b[^>]*>.*?</\1>", re.I | re.S
)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TAG = re.compile(r"<[^>]+>")
_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&laquo;": "«", "&raquo;": "»", "&mdash;": "—", "&ndash;": "–",
    "&rsquo;": "'", "&hellip;": "…", "&deg;": "°", "&euro;": "€", "&#8381;": "₽",
}

#: Blocks that are chrome on kase.kz and on issuer sites. Matched
#: case-insensitively against a *line*, not against the whole document, so a
#: sentence that merely mentions "контакты" survives.
_NAV_LINES = (
    "личный кабинет", "войти", "регистрация", "поиск по сайту", "карта сайта",
    "все права защищены", "cookie", "куки", "подписаться на рассылку",
    "версия для слабовидящих", "выберите язык", "мобильное приложение",
    "меню", "главная", "контакты", "вакансии", "пресс-центр", "обратная связь",
    "политика конфиденциальности", "пользовательское соглашение",
    "©", "all rights reserved", "skip to main content",
)


def strip_html(html: str) -> str:
    """HTML to text, keeping block structure and table cells apart."""
    text = _SCRIPT_STYLE.sub(" ", html)
    text = _COMMENT.sub(" ", text)
    text = re.sub(r"</(p|div|tr|li|h[1-6]|table|section|article)>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</t[dh]>", "\t", text, flags=re.I)
    text = _TAG.sub(" ", text)
    for entity, replacement in _ENTITIES.items():
        text = text.replace(entity, replacement)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    return text


def remove_navigation(text: str) -> str:
    """Drop chrome lines and repeated one-word menu items."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        lowered = stripped.lower()
        if any(marker in lowered for marker in _NAV_LINES) and len(stripped) < 80:
            continue
        # A short line with no verb-ish content and no digits is almost always
        # a menu item on these sites.
        if len(stripped) < 25 and not any(ch.isdigit() for ch in stripped) and stripped.count(" ") <= 1:
            continue
        kept.append(stripped)
    return "\n".join(kept)


def normalize_encoding(text: str) -> str:
    """NFKC, fix the usual mojibake, unify whitespace and dashes."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(" ", " ").replace("​", "").replace("﻿", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # cp1251 text decoded as latin-1 is the classic KASE-scrape failure.
    if "Ð" in text and "Ð°" in text:
        try:
            text = text.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Language
# --------------------------------------------------------------------------

_KK_ONLY = set("әғқңөұүһі")
_RU_CHARS = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
_LATIN = set("abcdefghijklmnopqrstuvwxyz")


def detect_language(text: str) -> str:
    """ru / kk / en / unknown, by character distribution.

    Deliberately simple: the corpus is Cyrillic Russian with a Kazakh minority
    and some English filings. A statistical detector would add a dependency for
    no measurable gain on this alphabet split.
    """
    lowered = text.lower()
    kk = sum(lowered.count(ch) for ch in _KK_ONLY)
    ru = sum(1 for ch in lowered if ch in _RU_CHARS)
    en = sum(1 for ch in lowered if ch in _LATIN)
    total = ru + en + kk
    if total < 20:
        return "unknown"
    if kk / max(1, ru + kk) > 0.02:
        return "kk"
    if ru >= en:
        return "ru"
    return "en"


# --------------------------------------------------------------------------
# Quality
# --------------------------------------------------------------------------

_BROKEN_MARKERS = ("�", "\x00", "ï¿½")


def is_broken(text: str) -> bool:
    if any(marker in text for marker in _BROKEN_MARKERS):
        return True
    if not text.strip():
        return True
    letters = sum(1 for ch in text if ch.isalpha())
    return letters / max(1, len(text)) < 0.25


def quality_score(text: str) -> float:
    """0..1. Blends length, letter ratio, line variety and digit density.

    Financial text is digit-heavy, so digits are not penalised - but a page
    that is *only* digits is a raw table dump that should have gone through the
    table extractor instead, and scores low.
    """
    if not text.strip():
        return 0.0
    score = 1.0
    length = len(text)
    if length < 200:
        score *= length / 200.0
    letters = sum(1 for ch in text if ch.isalpha())
    letter_ratio = letters / max(1, length)
    if letter_ratio < 0.5:
        score *= max(0.2, letter_ratio / 0.5)
    lines = [line for line in text.splitlines() if line.strip()]
    if lines:
        unique = len({line.strip().lower() for line in lines})
        score *= 0.4 + 0.6 * (unique / len(lines))
    if any(marker in text for marker in _BROKEN_MARKERS):
        score *= 0.2
    words = text.split()
    if words:
        avg_word = sum(len(w) for w in words) / len(words)
        if avg_word > 25 or avg_word < 2:
            score *= 0.5
    return round(max(0.0, min(1.0, score)), 3)


# --------------------------------------------------------------------------
# Normalisation of financial surface forms
# --------------------------------------------------------------------------

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}
_DATE_PATTERNS = (
    (re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b"), lambda m: f"{m[3]}-{m[2]}-{m[1]}"),
    (re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b"), lambda m: f"{m[3]}-{m[2]}-{m[1]}"),
    (
        re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", re.I),
        lambda m: f"{m[3]}-{_MONTHS[m[2].lower()]:02d}-{int(m[1]):02d}",
    ),
)

_CURRENCY = (
    (re.compile(r"(?<![A-Za-zА-Яа-я])тенге\b", re.I), "KZT"),
    (re.compile(r"₸"), "KZT"),
    (re.compile(r"(?<![A-Za-z])тг\.?(?![A-Za-zА-Яа-я])", re.I), "KZT"),
    (re.compile(r"долларов США|доллара США|долл\. США", re.I), "USD"),
    (re.compile(r"\$"), "USD"),
    (re.compile(r"(?<![A-Za-zА-Яа-я])евро\b", re.I), "EUR"),
    (re.compile(r"€"), "EUR"),
)


def normalize_dates(text: str) -> str:
    """Rewrite Russian and dotted dates to ISO, so the model sees one form."""
    for pattern, replacement in _DATE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def normalize_currency(text: str) -> str:
    for pattern, code in _CURRENCY:
        text = pattern.sub(code, text)
    # "1 234 567,89" -> "1234567.89": Russian thousands spaces and decimal comma
    text = re.sub(r"(?<=\d)[  ](?=\d{3}\b)", "", text)
    text = re.sub(r"(?<=\d),(?=\d{1,2}\b)", ".", text)
    return text


# --------------------------------------------------------------------------
# PII
# --------------------------------------------------------------------------

_PII = (
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), "[EMAIL]"),
    (re.compile(r"(?<!\d)(?:\+7|8)[\s(-]?\d{3}[\s)-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)"), "[PHONE]"),
    (re.compile(r"(?<!\d)\d{12}(?!\d)"), "[IIN_OR_BIN]"),
    (re.compile(r"\bKZ\d{2}[A-Z0-9]{16}\b"), "[IBAN]"),
)


def strip_pii(text: str) -> str:
    """Redact personal identifiers.

    Issuer BINs are 12 digits and get redacted along with individual IINs. That
    is the right trade: the BIN is recoverable from our own reference data, and
    a model that has memorised 12-digit identifiers is a liability.
    """
    for pattern, replacement in _PII:
        text = pattern.sub(replacement, text)
    return text


# --------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------

def text_fingerprint(text: str) -> str:
    normalised = " ".join(re.sub(r"\d+", "0", text.lower()).split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def shingles(text: str, size: int = 8) -> set[int]:
    words = re.sub(r"\W+", " ", text.lower()).split()
    if len(words) < size:
        return {hash(" ".join(words))} if words else set()
    return {hash(" ".join(words[i : i + size])) for i in range(len(words) - size + 1)}


def jaccard(left: set[int], right: set[int]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def deduplicate(
    items: Iterable[tuple[str, str]], *, near_threshold: float = 0.85
) -> tuple[list[str], dict[str, str]]:
    """Exact then near-duplicate removal.

    ``items`` is ``(id, text)``. Returns the surviving ids and a mapping of
    dropped id -> the id it duplicated, so the manifest can report *what* was
    removed rather than only how much.
    """
    kept: list[str] = []
    dropped: dict[str, str] = {}
    seen_exact: dict[str, str] = {}
    seen_shingles: list[tuple[str, set[int]]] = []

    for item_id, text in items:
        fingerprint = text_fingerprint(text)
        if fingerprint in seen_exact:
            dropped[item_id] = seen_exact[fingerprint]
            continue
        signature = shingles(text)
        duplicate_of = next(
            (other_id for other_id, other in seen_shingles
             if jaccard(signature, other) >= near_threshold),
            None,
        )
        if duplicate_of:
            dropped[item_id] = duplicate_of
            continue
        seen_exact[fingerprint] = item_id
        seen_shingles.append((item_id, signature))
        kept.append(item_id)
    return kept, dropped


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

@dataclass(slots=True)
class CleanResult:
    text: str
    language: str
    quality: float
    broken: bool
    fingerprint: str


def clean_document(raw: str, *, is_html: bool = False, redact_pii: bool = True) -> CleanResult:
    text = strip_html(raw) if is_html else raw
    text = normalize_encoding(text)
    if is_html:
        text = remove_navigation(text)
    text = normalize_dates(text)
    text = normalize_currency(text)
    if redact_pii:
        text = strip_pii(text)
    text = normalize_encoding(text)
    return CleanResult(
        text=text,
        language=detect_language(text),
        quality=quality_score(text),
        broken=is_broken(text),
        fingerprint=text_fingerprint(text),
    )


__all__ = [
    "CleanResult",
    "clean_document",
    "deduplicate",
    "detect_language",
    "is_broken",
    "jaccard",
    "normalize_currency",
    "normalize_dates",
    "normalize_encoding",
    "quality_score",
    "remove_navigation",
    "shingles",
    "strip_html",
    "strip_pii",
    "text_fingerprint",
]
