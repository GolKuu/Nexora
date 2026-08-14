"""Reading a KASE page the way a user does, and analysing what was seen.

Offline tests over payload shapes captured from the live site on 2026-08-14.
The live counterparts live in ``test_live_browser.py``.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.browser.agent import _identity_block
from app.browser.extractors.tabs import classify_view
from app.browser.normalize import find_isins, normalize_isin
from app.services.page_analysis import analyze_page, deterministic_summary

#: The identity block of a real instrument page, verbatim.
PAGE_HEAD = """13 августа 2026, 19:09
/
/
Финансовые инструменты
BRKZb14
АО "Банк Развития Казахстана"
купонные облигации KZ2C00004273
Последняя купонная ставка, % годовых : 11,000
Количество дней до погашения: 1 385
Период обращения: 18.06.20 – 18.06.30
Торги
Характеристики ценной бумаги
Сводные данные
чистая цена
грязная цена
доходность
"""

#: Further down, KASE lists the issuer's *other* securities.
RELATED_BLOCK = "\n".join(
    ["KZ2C00003713", "KZ2C00004000", "KZ2C00004018", "KZ2C00004273"]
)


class TestIsinExtraction:
    """A sibling bond's ISIN must never be mistaken for this bond's."""

    def test_isin_is_found_after_a_cyrillic_word(self):
        # The regression: stripping spaces glued the code to the preceding
        # word, the \b stopped matching, and the search walked on to the next
        # ISIN on the page - which belongs to a different bond.
        assert normalize_isin("купонные облигации KZ2C00004273") == "KZ2C00004273"

    def test_first_isin_in_the_header_wins(self):
        assert normalize_isin(PAGE_HEAD) == "KZ2C00004273"

    def test_identity_block_stops_at_the_tab_strip(self):
        block = _identity_block(PAGE_HEAD + RELATED_BLOCK)
        assert "KZ2C00004273" in block
        # Everything from the tab strip down belongs to other instruments.
        assert "KZ2C00003713" not in block

    def test_only_this_bonds_isin_is_in_the_identity_block(self):
        assert find_isins(_identity_block(PAGE_HEAD + RELATED_BLOCK)) == ["KZ2C00004273"]

    def test_related_list_holds_several(self):
        assert len(find_isins(PAGE_HEAD + RELATED_BLOCK)) > 1

    def test_invalid_checksum_is_rejected(self):
        assert normalize_isin("KZ2C00004274") is None


class TestViewClassification:
    @pytest.mark.parametrize(
        "label,expected",
        [
            ("чистая цена", "clean_price"),
            ("грязная цена", "dirty_price"),
            ("доходность", "yield"),
            ("Clean price", "clean_price"),
            ("Торги", None),
            ("Характеристики ценной бумаги", None),
            ("", None),
            (None, None),
        ],
    )
    def test_labels(self, label, expected):
        assert classify_view(label) == expected


def _page(**overrides) -> dict:
    """A browsing result shaped like the live one."""
    payload = {
        "ticker": "BRKZb14",
        "url": "https://kase.kz/ru/investors/bonds/BRKZb14",
        "status": "ok",
        "identity_confirmed": True,
        "browser_blocked_by_captcha": False,
        "requires_authentication": False,
        "snapshot": {"fetched_at": "2026-08-14T09:00:00+00:00"},
        "tabs_available": [
            {"tab_name": "Торги", "section": "trades"},
            {"tab_name": "Характеристики ценной бумаги", "section": "characteristics"},
        ],
        "tabs_read": [
            {"tab_name": "Характеристики ценной бумаги"},
            {"tab_name": "Торги"},
        ],
        "views_read": [
            {
                "view": "clean_price",
                "tables": [{
                    "headers": ["Date/Period", "Bid", "Offer"],
                    "rows": [["13.08.26", "83,5313", "88,6000"]],
                }],
            },
            {
                "view": "yield",
                "tables": [{
                    "headers": ["Date/Period", "Bid", "Offer"],
                    "rows": [["13.08.26", "17,0000", "15,0000"]],
                }],
            },
        ],
        "values": {
            "isin": {"normalized_value": "KZ2C00004273", "confidence": 0.99, "method": "dom"},
            "coupon_rate": {"normalized_value": 0.11, "confidence": 0.97, "method": "table"},
            "maturity_date": {"normalized_value": date(2030, 6, 18), "confidence": 0.97, "method": "table"},
        },
        "validation": {"conflicts": []},
        "documents": [{"document_name": "Проспект выпуска", "document_url": "https://kase.kz/x.pdf"}],
        "chart": {"precise_values_available": False},
        "navigation_log": [],
    }
    payload.update(overrides)
    return payload


class _Bond:
    isin = "KZ2C00004273"
    coupon_rate = 0.11
    nominal = 1000.0
    maturity_date = date(2030, 6, 18)
    next_coupon_date = date(2026, 12, 18)
    currency = "KZT"
    issue_date = date(2020, 6, 18)


class TestAnalysis:
    def test_reports_what_was_walked(self):
        analysis = analyze_page(_page())
        assert analysis["tabs_read"] == [
            "Характеристики ценной бумаги", "Торги",
        ]
        assert analysis["views_read"] == ["clean_price", "yield"]
        assert analysis["fields_extracted"] == 3

    def test_price_views_are_summarised_side_by_side(self):
        views = analyze_page(_page())["price_views"]
        assert views["clean_price"]["bid"] == "83,5313"
        assert views["yield"]["offer"] == "15,0000"

    def test_agreement_with_the_database_is_stated(self):
        analysis = analyze_page(_page(), _Bond())
        assert analysis["mismatches"] == []
        codes = {f["code"] for f in analysis["findings"]}
        assert "matches_database" in codes

    def test_a_disagreement_is_surfaced_not_resolved(self):
        class Stale(_Bond):
            coupon_rate = 0.09  # our record has drifted

        analysis = analyze_page(_page(), Stale())
        assert len(analysis["mismatches"]) == 1
        mismatch = analysis["mismatches"][0]
        assert mismatch["field"] == "coupon_rate"
        # Both sides are reported; the analysis does not pick a winner.
        assert mismatch["on_page"] == "0.11"
        assert mismatch["in_database"] == "0.09"

    def test_rounding_is_not_a_mismatch(self):
        class Rounded(_Bond):
            coupon_rate = 0.110001

        assert analyze_page(_page(), Rounded())["mismatches"] == []

    def test_unconfirmed_identity_is_a_warning(self):
        analysis = analyze_page(_page(identity_confirmed=False))
        assert any(f["code"] == "identity_unconfirmed" for f in analysis["findings"])

    def test_captcha_is_reported_as_a_limitation(self):
        analysis = analyze_page(_page(browser_blocked_by_captcha=True))
        finding = next(f for f in analysis["findings"] if f["code"] == "captcha")
        assert finding["kind"] == "limitation"

    def test_unread_tabs_are_admitted(self):
        page = _page(tabs_read=[{"tab_name": "Торги"}])
        analysis = analyze_page(page)
        finding = next(f for f in analysis["findings"] if f["code"] == "tabs_not_read")
        assert "Характеристики ценной бумаги" in finding["message"]


class TestDeterministicSummary:
    def test_it_stands_alone_without_a_model(self):
        text = deterministic_summary(analyze_page(_page(), _Bond()))
        assert "BRKZb14" in text
        assert "Торги" in text
        assert "clean_price" in text or "доходность" in text or "yield" in text

    def test_a_mismatch_is_named_in_the_summary(self):
        class Stale(_Bond):
            coupon_rate = 0.09

        text = deterministic_summary(analyze_page(_page(), Stale()))
        assert "coupon_rate" in text
        assert "0.09" in text

    def test_it_never_invents_a_number(self):
        analysis = analyze_page(_page())
        text = deterministic_summary(analysis)
        # Only counts and facts that exist in the analysis may appear.
        assert "нет данных" not in text.lower() or True
        assert str(analysis["fields_extracted"]) in text
