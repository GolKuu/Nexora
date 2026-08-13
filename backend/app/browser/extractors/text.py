"""KaseTextExtractor - main content out, chrome out of the way (§34).

Navigation menus, cookie banners, footers and the "you might also like" strip
repeat on every page. Left in, they poison text search and waste model tokens.
Removed silently, they make debugging impossible - so the raw text is kept
alongside the cleaned text, always.

The filter works on line semantics (repetition, known chrome phrases, length),
not on CSS class names, so a redesign does not break it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Lines that are unmistakably site chrome on kase.kz. Compared case-folded and
#: whitespace-normalised; a line matches only if it *equals* one of these.
CHROME_LINES = {
    # global navigation
    "рынки", "индексы", "инвесторам", "эмитентам", "участникам торгов",
    "клиринг и расчеты", "информация", "медиацентр", "правила", "о бирже",
    "главная", "войти", "выйти", "поиск", "меню", "закрыть",
    "markets", "indexes", "investors", "issuers", "information", "rules",
    "media center", "about", "home", "log in", "search", "menu", "close",
    # language switch
    "kz", "ru", "en",
    # footer / promo
    "актуальная информация", "подробнее", "казахстанская фондовая биржа",
    "faq", "карьера", "контакты и реквизиты", "карта сайта",
    "информационный терминал", "iris finance",
    "правила kase", "члены биржи", "итоги торгов", "просмотр торгов",
    "регламент торгов и клиринга", "новости рынков и компаний",
    "список инструментов", "индикаторы денежного рынка", "эмитенты",
    "more", "read more", "sitemap", "career", "contacts",
}

#: Substrings that mark a cookie/consent banner in either language.
COOKIE_MARKERS = (
    "cookie", "куки", "файлы cookie", "мы используем файл",
    "согласие на обработку", "consent",
)

#: Footer boilerplate - once one of these is seen, the rest of the page is
#: legal text, not content.
FOOTER_MARKERS = (
    "копирование материалов",
    "все права защищены",
    "all rights reserved",
)

_WS = re.compile(r"[ \t ]+")


def _norm(line: str) -> str:
    return _WS.sub(" ", line).strip()


@dataclass(slots=True)
class ExtractedText:
    raw_text: str
    main_text: str
    lines_total: int = 0
    lines_kept: int = 0
    removed_reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "main_text": self.main_text,
            "raw_text": self.raw_text,
            "lines_total": self.lines_total,
            "lines_kept": self.lines_kept,
            "removed": self.removed_reasons,
        }


class KaseTextExtractor:
    """Strips navigation noise while keeping the raw text for debugging."""

    version = "1.0.0"

    def __init__(self, *, keep_repeated_after: int = 3) -> None:
        #: A line that appears more than this often on one page is a template
        #: artefact (a repeated menu), not content.
        self.keep_repeated_after = keep_repeated_after

    def extract(self, raw_text: str) -> dict:
        return self.extract_object(raw_text).as_dict()

    def extract_object(self, raw_text: str) -> ExtractedText:
        lines = raw_text.splitlines()
        normalised = [_norm(line) for line in lines]
        counts: dict[str, int] = {}
        for line in normalised:
            if line:
                key = line.casefold()
                counts[key] = counts.get(key, 0) + 1

        kept: list[str] = []
        removed: dict[str, int] = {}

        def drop(reason: str) -> None:
            removed[reason] = removed.get(reason, 0) + 1

        footer_reached = False
        for line in normalised:
            if not line:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            folded = line.casefold()

            if footer_reached:
                drop("footer")
                continue
            if any(marker in folded for marker in FOOTER_MARKERS):
                footer_reached = True
                drop("footer")
                continue
            if folded in CHROME_LINES:
                drop("navigation")
                continue
            if any(marker in folded for marker in COOKIE_MARKERS) and len(line) < 300:
                drop("cookie_banner")
                continue
            if counts.get(folded, 0) > self.keep_repeated_after and len(line) < 40:
                drop("repeated")
                continue
            kept.append(line)

        main = "\n".join(kept).strip()
        main = re.sub(r"\n{3,}", "\n\n", main)
        return ExtractedText(
            raw_text=raw_text,
            main_text=main,
            lines_total=len([line for line in normalised if line]),
            lines_kept=len([line for line in kept if line]),
            removed_reasons=removed,
        )


def label_value_pairs(text: str) -> dict[str, str]:
    """Parse ``Label<TAB>Value`` and ``Label: Value`` lines out of page text.

    KASE renders its parameter tables as two-column rows, which come out of the
    DOM as tab-separated lines. This is the cheap path to the same facts the
    table extractor produces, and a useful cross-check against it (§30).
    """
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "\t" in line:
            label, _, value = line.partition("\t")
        elif ": " in line and len(line) < 240:
            label, _, value = line.partition(": ")
        else:
            continue
        label, value = _norm(label), _norm(value)
        if label and value and len(label) < 160:
            pairs.setdefault(label, value)
    return pairs
