"""Browser-layer tests that need no network.

Two groups:

* pure logic - normalisation, the text extractor, the validator, label mapping.
  These run everywhere.
* real-browser tests against locally generated HTML. They exercise the actual
  Playwright code paths (table reading, tab clicking, document discovery,
  CAPTCHA detection) without touching kase.kz, and skip when the engine is not
  installed on the machine.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.browser.extractors.fields import values_from_labels
from app.browser.extractors.text import KaseTextExtractor, label_value_pairs
from app.browser.locators import LocatorTarget
from app.browser.normalize import (
    normalize_isin,
    normalize_ticker,
    parse_currency,
    parse_date,
    parse_money,
    parse_number,
    parse_percent,
)
from app.browser.types import ExtractionMethod, SourceRef
from app.browser.validator import DataValidator, make_value

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# -- normalisation (§44, §45, §46) -------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1 234,56", 1234.56),
        ("3 000 000 000", 3_000_000_000.0),
        ("98,4900", 98.49),
        ("-2,38", -2.38),
        ("1,234.56", 1234.56),
        ("–", None),
        ("", None),
        ("нет данных", None),
    ],
)
def test_parse_number_handles_the_formats_kase_prints(raw, expected):
    assert parse_number(raw) == expected


def test_percent_becomes_a_decimal_fraction():
    assert parse_percent("18,45 %") == pytest.approx(0.1845)
    assert parse_percent("19,500") == pytest.approx(0.195)


def test_raw_value_is_never_lost():
    """§44: the normalized value is an addition, not a replacement."""
    value = make_value("coupon_rate", "18,45 %", parse_percent("18,45 %"))
    payload = value.as_dict()
    assert payload["raw_value"] == "18,45 %"
    assert payload["normalized_value"] == pytest.approx(0.1845)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("11.07.24", date(2024, 7, 11)),
        ("11.07.2027", date(2027, 7, 11)),
        ("2026-08-12", date(2026, 8, 12)),
        ("12 августа 2026", date(2026, 8, 12)),
        ("11.07.27–23.07.27", date(2027, 7, 11)),
        ("не указано", None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_currency_aliases_collapse_to_iso_codes():
    assert parse_currency("млн ₸") == "KZT"
    assert parse_currency("тенге") == "KZT"
    assert parse_currency("KZT") == "KZT"
    assert parse_currency("$") == "USD"


def test_money_applies_scale_words():
    amount, currency = parse_money("1 680,7 млн KZT")
    assert amount == pytest.approx(1_680_700_000.0)
    assert currency == "KZT"


def test_isin_checksum_is_verified():
    assert normalize_isin("KZ2P00011364") == "KZ2P00011364"
    assert normalize_isin("ISIN: KZ2P00011364") == "KZ2P00011364"
    # Same shape, wrong check digit - refused rather than accepted.
    assert normalize_isin("KZ2P00011365") is None


def test_ticker_normalisation_rejects_prose():
    assert normalize_ticker(" ACARb1 ") == "ACARb1"
    assert normalize_ticker("ТОО \"A-cars\" купонные облигации") is None


# -- text extractor (§34) ----------------------------------------------------

PAGE_TEXT = """12 августа 2026, 21:21
RU
KZ
EN
Рынки
Индексы
Инвесторам
Главная
ACARb1
ТОО "A-cars"
купонные облигации KZ2P00011364
Последняя купонная ставка, % годовых : 19,500
Количество дней до погашения: 329
Подробнее
Подробнее
Подробнее
Подробнее
АО "Казахстанская фондовая биржа" © 1993-2026 Копирование материалов - только с письменного разрешения.
"""


def test_text_extractor_drops_chrome_and_keeps_content():
    result = KaseTextExtractor().extract_object(PAGE_TEXT)
    assert "ACARb1" in result.main_text
    assert "Последняя купонная ставка" in result.main_text
    assert "Рынки" not in result.main_text
    assert "Подробнее" not in result.main_text
    assert "Копирование материалов" not in result.main_text


def test_text_extractor_keeps_raw_text_for_debugging():
    result = KaseTextExtractor().extract_object(PAGE_TEXT)
    assert result.raw_text == PAGE_TEXT
    assert result.lines_kept < result.lines_total


def test_label_value_pairs_reads_both_layouts():
    pairs = label_value_pairs(
        "Валюта выпуска и обслуживания\tKZT\nКоличество дней до погашения: 329\n"
    )
    assert pairs["Валюта выпуска и обслуживания"] == "KZT"
    assert pairs["Количество дней до погашения"] == "329"


# -- label mapping -----------------------------------------------------------

REAL_LABELS = {
    "Наименование облигации": "купонные облигации",
    "Валюта выпуска и обслуживания": "KZT",
    "Номинальная стоимость в валюте выпуска": "1 000,00",
    "Issue volume, KZT": "3 000 000 000",
    "ISIN": "KZ2P00011364",
    "Вид купонной ставки": "фиксированная",
    "Текущая купонная ставка, % годовых": "19,500",
    "Дата начала обращения": "11.07.24",
    "Период погашения": "11.07.27–23.07.27",
    "Код бумаги": "ACARb1",
    "Период ближайшей купонной выплаты": "11.01.27 – 22.01.27",
    "Совершенно новый параметр KASE": "42",
}


def test_known_labels_map_to_typed_fields():
    values, unmapped = values_from_labels(REAL_LABELS)
    by_field = {v.field: v for v in values}
    assert by_field["currency"].normalized == "KZT"
    assert by_field["isin"].normalized == "KZ2P00011364"
    assert by_field["coupon_rate"].normalized == pytest.approx(0.195)
    assert by_field["coupon_type"].normalized == "fixed"
    assert by_field["issue_date"].normalized == date(2024, 7, 11)
    assert by_field["maturity_date"].normalized == date(2027, 7, 11)
    assert by_field["next_coupon_date"].normalized == date(2027, 1, 11)
    assert by_field["nominal"].normalized == pytest.approx(1000.0)
    assert by_field["issue_size"].normalized == pytest.approx(3e9)
    assert by_field["ticker"].normalized == "ACARb1"


def test_unknown_labels_are_kept_not_discarded():
    """A parameter KASE adds tomorrow must still be visible today."""
    _values, unmapped = values_from_labels(REAL_LABELS)
    assert unmapped["Совершенно новый параметр KASE"] == "42"


# -- validator (§29, §30, §31) -----------------------------------------------


def _source(section: str) -> SourceRef:
    return SourceRef(page_url="https://kase.kz/ru/investors/bonds/X", section=section)


def test_visual_reading_may_not_supply_a_price():
    """§14: a number 'seen' on a chart is not a market value."""
    result = DataValidator().validate(
        [
            make_value(
                "clean_price",
                "около 101",
                101.0,
                method=ExtractionMethod.VISUAL.value,
                source=_source("chart"),
            )
        ]
    )
    assert "clean_price" not in result.accepted
    assert result.rejected
    assert "visual" in result.rejected[0].message


def test_precise_source_wins_over_visual_one():
    """§29: visual interpretation never silently replaces exact data."""
    result = DataValidator().validate(
        [
            make_value(
                "ytm", "около 18%", 0.18,
                method=ExtractionMethod.VISUAL.value, source=_source("chart"),
            ),
            make_value(
                "ytm", "18,45 %", 0.1845,
                method=ExtractionMethod.TABLE.value, source=_source("trades"),
            ),
        ]
    )
    accepted = result.accepted["ytm"]
    assert accepted.normalized == pytest.approx(0.1845)
    assert accepted.method == ExtractionMethod.TABLE.value


def test_conflicting_values_produce_a_warning_not_a_coin_flip():
    """§30: two sources disagreeing is information, not noise."""
    result = DataValidator().validate(
        [
            make_value("ytm", "18,45 %", 0.1845,
                       method=ExtractionMethod.TABLE.value, source=_source("список")),
            make_value("ytm", "17,90 %", 0.1790,
                       method=ExtractionMethod.DOM.value, source=_source("карточка")),
        ]
    )
    assert result.warnings, "a disagreement must be recorded"
    assert "differs between sources" in result.warnings[0].message
    # A value is still chosen - by priority - but trusted less.
    assert result.accepted["ytm"].confidence <= 0.6


def test_agreeing_sources_do_not_warn():
    result = DataValidator().validate(
        [
            make_value("coupon_rate", "19,500", 0.195,
                       method=ExtractionMethod.TABLE.value, source=_source("a")),
            make_value("coupon_rate", "19,5 %", 0.195,
                       method=ExtractionMethod.DOM.value, source=_source("b")),
        ]
    )
    assert not result.warnings
    assert result.accepted["coupon_rate"].confidence > 0.9


def test_implausible_numbers_are_refused():
    result = DataValidator().validate(
        [make_value("coupon_rate", "1950", 19.5, method=ExtractionMethod.TABLE.value)]
    )
    assert "coupon_rate" not in result.accepted
    assert "outside the plausible range" in result.rejected[0].message


def test_confidence_reflects_the_extraction_method():
    """§31: the method a value came from sets how much it is worth."""
    dom = make_value("x", "1", 1.0, method=ExtractionMethod.DOM.value)
    visual = make_value("y", "1", 1.0, method=ExtractionMethod.VISUAL.value)
    assert dom.confidence > 0.95
    assert visual.confidence < 0.5


# -- real browser against local HTML -----------------------------------------

LOCAL_PAGE = """
<!doctype html><html lang="ru"><head><title>Тестовый выпуск TEST1</title></head>
<body>
  <h1>TEST1</h1>
  <div role="tablist">
    <div role="tab" class="tab active" id="t1">Торги</div>
    <div role="tab" class="tab" id="t2">Характеристики ценной бумаги</div>
  </div>
  <div id="p1">
    <table><thead><tr><th>Дата сделки</th><th>Цена, значение</th></tr></thead>
      <tbody><tr><td>12.08.26</td><td>98,5000</td></tr>
             <tr><td>11.08.26</td><td>96,0000</td></tr></tbody></table>
  </div>
  <div id="p2" style="display:none">
    <table><tbody>
      <tr><td>ISIN</td><td>KZ2P00011364</td></tr>
      <tr><td>Текущая купонная ставка, % годовых</td><td>19,500</td></tr>
    </tbody></table>
    <a href="/files/emitters/TEST/prospectus_2024.pdf">Проспект выпуска облигаций (TEST1)</a>
  </div>
  <script>
    document.getElementById('t2').addEventListener('click', () => {
      document.getElementById('p1').style.display = 'none';
      document.getElementById('p2').style.display = 'block';
    });
  </script>
</body></html>
"""


@pytest.fixture
async def local_session():
    """A real browser session serving locally generated HTML."""
    from app.browser.session import BrowserService, BrowserUnavailableError

    service = BrowserService()
    try:
        session = await service.new_session(label="test")
    except BrowserUnavailableError as exc:
        pytest.skip(f"browser engine not installed here: {exc}")
    try:
        yield session
    finally:
        await service.aclose()


async def test_tables_are_read_into_structured_records(local_session):
    from app.browser.extractors.tables import extract_tables

    await local_session.page.set_content(LOCAL_PAGE)
    tables = await extract_tables(local_session, section="trades")
    trades = next(t for t in tables if "Дата сделки" in t.headers)
    assert trades.records[0] == {"Дата сделки": "12.08.26", "Цена, значение": "98,5000"}
    assert trades.source is not None and trades.source.section == "trades"


async def test_tabs_are_discovered_and_opening_one_changes_the_content(local_session):
    from app.browser.extractors.tabs import KaseTabExplorer

    await local_session.page.set_content(LOCAL_PAGE)
    explorer = KaseTabExplorer(local_session)
    discovered = await explorer.discover()
    names = {tab["tab_name"] for tab in discovered}
    assert {"Торги", "Характеристики ценной бумаги"} <= names
    assert any(tab["section"] == "characteristics" for tab in discovered)

    target = next(t for t in discovered if t["section"] == "characteristics")
    result = await explorer.open_tab(target)
    assert result.status == "ok"
    assert result.changed_content, "the click must actually reveal new content"
    assert "19,500" in result.text


async def test_document_links_are_captured_with_their_source_page(local_session):
    from app.browser.extractors.documents import extract_documents

    await local_session.page.set_content(LOCAL_PAGE)
    documents = await extract_documents(local_session, section="Характеристики")
    assert documents
    document = documents[0]
    assert document.document_type == "pdf"
    assert document.name.startswith("Проспект")
    assert document.section == "Характеристики"
    assert document.as_dict()["document_url"].endswith("prospectus_2024.pdf")


async def test_a_captcha_is_reported_and_never_solved(local_session):
    """§21: the agent stops at the wall and says so."""
    await local_session.page.set_content(
        "<html><body><h1>Подтвердите, что вы не робот</h1>"
        "<div class='g-recaptcha'></div></body></html>"
    )
    blocks = await local_session.detect_blocks()
    assert blocks["browser_blocked_by_captcha"] is True

    snapshot = await local_session.snapshot()
    assert snapshot.status == "blocked_by_captcha"


async def test_a_login_wall_is_reported_not_worked_around(local_session):
    """§22: authentication required is a status, not a challenge."""
    await local_session.page.set_content(
        "<html><body><p>Требуется авторизация</p></body></html>"
    )
    blocks = await local_session.detect_blocks()
    assert blocks["requires_authentication"] is True


async def test_missing_elements_produce_a_clear_error_not_a_blind_click(local_session):
    """§41: after exhausting the strategies, say so - never click at random."""
    await local_session.page.set_content(LOCAL_PAGE)
    result = await local_session.click_text("Такой вкладки нет")
    assert not result.ok
    assert "not found" in (result.error or "")


async def test_every_action_is_logged(local_session):
    """§40: the navigation log is what makes a flow debuggable."""
    await local_session.page.set_content(LOCAL_PAGE)
    await local_session.click_text("Характеристики ценной бумаги")
    await local_session.get_element_text(LocatorTarget(css="h1"))
    events = local_session.navigation_log_dicts()
    assert events
    assert all(event["session_id"] == local_session.id for event in events)
    assert [event["action_number"] for event in events] == list(
        range(1, len(events) + 1)
    )
    assert all("duration_ms" in event for event in events)


async def test_the_toolbox_refuses_unknown_commands(local_session):
    from app.browser.toolbox import BrowserToolbox

    toolbox = BrowserToolbox(local_session)
    assert "browser.open" in toolbox.command_names
    assert "browser.extract_table" in toolbox.command_names

    result = await toolbox.run("browser.launch_missiles")
    assert not result.ok
    assert "unknown browser command" in (result.error or "")


async def test_a_planned_sequence_stops_at_the_first_failure(local_session):
    """§38: a planner proposes, the toolbox executes, failures surface."""
    from app.browser.toolbox import BrowserToolbox

    await local_session.page.set_content(LOCAL_PAGE)
    toolbox = BrowserToolbox(local_session)
    results = await toolbox.run_plan(
        [
            {"action": "browser.click", "args": {"target": "Характеристики ценной бумаги"}},
            {"action": "browser.click", "args": {"target": "Несуществующая вкладка"}},
            {"action": "browser.get_title", "args": {}},
        ]
    )
    assert len(results) == 2, "execution must stop at the failing step"
    assert results[0].ok and not results[1].ok


# -- cache (§24) -------------------------------------------------------------


def test_page_cache_expires_by_ttl():
    from app.browser.cache import BrowserPageCache

    cache = BrowserPageCache()
    cache.put("https://kase.kz/x", {"a": 1}, kind="bond", ttl=0.0)
    assert cache.get("https://kase.kz/x", "bond") is None

    cache.put("https://kase.kz/y", {"a": 2}, kind="bond", ttl=60.0)
    entry = cache.get("https://kase.kz/y", "bond")
    assert entry is not None and entry.value == {"a": 2}


def test_cache_invalidation_is_scoped_to_one_url():
    from app.browser.cache import BrowserPageCache

    cache = BrowserPageCache()
    cache.put("https://kase.kz/a", 1, kind="bond")
    cache.put("https://kase.kz/b", 2, kind="bond")
    assert cache.invalidate("https://kase.kz/a") == 1
    assert cache.get("https://kase.kz/b", "bond") is not None


# -- politeness (§23) --------------------------------------------------------


async def test_pacer_enforces_a_minimum_interval():
    import time

    from app.browser.pacing import RequestPacer

    pacer = RequestPacer(min_interval_ms=120, max_concurrency=1)
    started = time.monotonic()
    for _ in range(3):
        async with pacer.slot():
            pass
    assert time.monotonic() - started >= 0.24


def test_runtime_budget_expires():
    from app.browser.pacing import RuntimeBudget

    budget = RuntimeBudget(0.0)
    assert budget.exhausted
    assert budget.check("anything") is False


# -- configuration (§2, §47) -------------------------------------------------


def test_browser_mode_needs_no_api_key():
    """§2: the public site is public. No key, no excuse not to read it."""
    from app.core.config import Settings
    from app.providers.factory import build_provider
    from app.providers.kase_browser import KaseBrowserProvider

    config = Settings(KASE_DATA_MODE="browser", KASE_API_KEY=None, APP_ENV="production")
    assert config.validate_runtime() == []
    assert isinstance(build_provider(config), KaseBrowserProvider)


def test_auto_mode_puts_the_browser_ahead_of_the_plain_html_reader():
    """§48: structured source, then browser, then the rest."""
    from app.core.config import Settings
    from app.providers.composite import CompositeKaseProvider
    from app.providers.factory import build_provider

    provider = build_provider(
        Settings(KASE_DATA_MODE="auto", KASE_API_KEY=None, APP_ENV="production")
    )
    assert isinstance(provider, CompositeKaseProvider)
    names = [p.name for p in provider.providers]
    assert names.index("kase_browser") < names.index("kase_website")
    assert "mock_kase" not in names


def test_browser_mode_never_falls_back_to_mock():
    """§47/§48: in browser mode the data really does come from a browser."""
    from app.core.config import Settings
    from app.providers.factory import build_provider

    provider = build_provider(Settings(KASE_DATA_MODE="browser", APP_ENV="development"))
    assert provider.name == "kase_browser"
    assert provider.is_mock is False
