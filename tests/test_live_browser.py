"""The §55 browser integration scenario, against the real kase.kz.

Run explicitly, never by accident:

    RUN_LIVE_KASE_BROWSER_TESTS=true pytest -m live_browser -s

The scenario is one continuous session, in order:

 1. start a browser                     8. discover the page's own tabs
 2. open the official KASE site         9. open at least one of them
 3. confirm the domain                 10. read the visible text
 4. open the bond catalogue            11. take a screenshot
 5. extract a real ticker              12. confirm source = KASE
 6. open that instrument's page        13. close the session
 7. read at least one field

Each step prints what it actually found, so a failure tells you what KASE
looked like at that moment rather than just that an assertion failed.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.live_browser,
    pytest.mark.anyio,
    pytest.mark.skipif(
        not any(
            os.getenv(name, "false").lower() == "true"
            for name in (
                "RUN_LIVE_KASE_BROWSER_TESTS",
                "RUN_LIVE_BROWSER_TESTS",
            )
        ),
        reason="RUN_LIVE_KASE_BROWSER_TESTS is not enabled",
    ),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def agent():
    from app.browser.session import BrowserService, BrowserUnavailableError
    from app.browser.agent import KaseBrowserAgent

    service = BrowserService()
    try:
        session = await service.new_session(label="live-test")
    except BrowserUnavailableError as exc:
        pytest.skip(f"browser engine unavailable: {exc}")
    try:
        yield KaseBrowserAgent(session)
    finally:
        # Step 13: the session is always closed, pass or fail.
        await service.aclose()
        assert not service.running


async def test_full_public_browser_flow_on_kase(agent):
    report: dict = {}

    # 2-3. Open the official site and confirm we are really on it.
    home = agent.url_for("home")
    navigation = await agent._goto(home, min_chars=500)
    if navigation.get("browser_blocked_by_captcha"):
        pytest.skip("kase.kz served a CAPTCHA; the agent correctly refuses to solve it")
    assert navigation["ok"], f"kase.kz did not load: {navigation}"
    current = await agent.session.get_current_url()
    assert agent.confirms_domain(current), current
    report["home"] = current
    report["title"] = await agent.session.get_page_title()

    # 4-5. The catalogue, and a real ticker out of it - nothing hardcoded.
    catalog = await agent.discover_catalog(limit=40, use_cache=False)
    assert catalog.status == "ok", catalog.error
    assert catalog.entries, "the catalogue page produced no instruments"
    entry = catalog.entries[0]
    assert entry.ticker
    assert agent.confirms_domain(entry.url)
    report["catalog_url"] = catalog.snapshot.url if catalog.snapshot else None
    report["catalog_count"] = len(catalog.entries)
    report["ticker"] = entry.ticker
    report["isin_from_catalog"] = entry.isin

    # 6-11. The instrument page: identity, text, tabs, fields, screenshot.
    result = await agent.open_bond(
        entry.ticker,
        url=entry.url,
        max_tabs=2,
        with_screenshot=True,
        use_cache=False,
    )
    assert result.status == "ok", result.error
    assert result.identity_confirmed, f"{entry.ticker} not confirmed on {result.url}"
    assert result.content is not None

    # 10. visible text
    assert len(result.content.main_text) > 200
    assert entry.ticker.casefold() in result.content.main_text.casefold()

    # 7. at least one field read out of the DOM
    accepted = result.validation.accepted if result.validation else {}
    assert accepted, "no field could be extracted from the instrument page"
    report["fields"] = {
        name: (value.raw, value.normalized, value.method)
        for name, value in accepted.items()
    }

    # A table really was parsed into records.
    tables = [t for t in result.content.tables if t.rows]
    assert tables, "no HTML table was parsed on the instrument page"
    report["tables"] = [
        {"headers": t.headers[:6], "rows": len(t.rows)} for t in tables
    ]

    # 8-9. tabs discovered and at least one opened
    assert result.tabs_available, "no tabs were discovered on the page"
    report["tabs_available"] = [t["tab_name"] for t in result.tabs_available]
    assert result.tabs_read, "no tab could be opened"
    opened = result.tabs_read[0]
    assert opened.status == "ok", opened.error
    assert opened.text, f"tab {opened.tab_name} produced no text"
    report["tab_read"] = {
        "name": opened.tab_name,
        "changed_content": opened.changed_content,
        "chars": len(opened.text),
        "tables": len(opened.tables),
    }

    # 11. screenshot
    assert result.screenshots, "no screenshot was taken"
    report["screenshot"] = result.screenshots[0]

    # documents, when the instrument has any
    report["documents"] = [d.as_dict() for d in result.documents][:5]

    # Safe network metadata: official KASE requests only, without request
    # headers, cookies, tokens or response bodies.
    structured_requests = agent.session.observed_endpoints()
    assert all(agent.confirms_domain(item["url"]) for item in structured_requests)
    report["structured_requests"] = structured_requests[:20]

    # 12. every value is stamped with the official page it came from.
    for value in accepted.values():
        assert value.source is not None
        assert agent.confirms_domain(value.source.page_url), value.source.page_url
        assert value.source.extractor_version
        assert value.source.browser_session_id == agent.session.id

    # And the navigation log recorded the path taken.
    assert result.navigation_log
    report["actions"] = len(result.navigation_log)

    print("\n--- live KASE browser flow -------------------------------------")
    for key, value in report.items():
        print(f"{key}: {value}")


async def test_the_agent_refuses_to_browse_anything_but_kase(agent):
    """§54: the browser agent is not a general-purpose web client."""
    result = await agent.open_bond("X", url="https://example.com/bonds/X")
    assert result.status == "error"
    assert "non-KASE host" in (result.error or "")


async def test_public_data_needs_no_api_key(agent):
    """§2: the whole flow above ran without KASE_API_KEY being set."""
    from app.core.config import settings

    if settings.KASE_API_KEY:
        pytest.skip("a KASE_API_KEY is configured in this environment")
    catalog = await agent.discover_catalog(limit=5, use_cache=False)
    assert catalog.entries


async def test_verify_on_kase_endpoint_writes_validated_data(agent, session, api):
    """The whole §38 chain, end to end: browse -> validate -> database -> API.

    A real instrument taken from the live catalogue is inserted into the test
    database, then refreshed through the public endpoint the "Проверить на
    KASE" button calls.
    """
    from app.models.bond import Bond
    from app.models.browser import BrowserNavigationLog, RawBrowserSnapshot
    from app.models.issuer import Issuer

    catalog = await agent.discover_catalog(limit=5, use_cache=False)
    assert catalog.entries
    entry = catalog.entries[0]

    issuer = Issuer(code="LIVETEST", name="Live browser test issuer")
    session.add(issuer)
    session.flush()
    session.add(
        Bond(
            ticker=entry.ticker,
            name=entry.issuer_name or entry.ticker,
            issuer_id=issuer.id,
            currency=entry.currency or "KZT",
            kase_url=entry.url,
        )
    )
    session.commit()

    try:
        response = api.post(
            f"/bonds/{entry.ticker}/verify-on-kase", json={"max_tabs": 2}
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        # What the user is shown: a source and a time, no automation detail.
        assert payload["source"] == "KASE"
        assert payload["checked_at_label"].startswith("Проверено на KASE:")
        assert "Playwright" not in str(payload)
        assert payload["ok"] is True
        assert payload["identity_confirmed"] is True
        assert payload["fields"], "no field survived validation"

        # Every field states how it was read and how much that is worth.
        for field in payload["fields"].values():
            assert field["method"] in {"dom", "table", "tooltip", "document"}
            assert 0.0 < field["confidence"] <= 1.0
            assert field["raw_value"] is not None
            assert field["source"]["page_url"].startswith("https://kase.kz")

        # Provenance persisted, both the page and the click path.
        snapshots = session.query(RawBrowserSnapshot).filter_by(key=entry.ticker).all()
        assert snapshots
        assert snapshots[0].html_hash and snapshots[0].visible_text
        assert session.query(BrowserNavigationLog).count() > 0

        # And the validated values actually landed on the bond row.
        session.expire_all()
        bond = session.query(Bond).filter_by(ticker=entry.ticker).one()
        assert bond.source == "kase_browser"
        assert bond.source_url and bond.source_url.startswith("https://kase.kz")
        assert bond.fetched_at is not None

        print(f"\nverified {entry.ticker}: {len(payload['fields'])} fields, "
              f"{len(payload['documents'])} documents, {payload['checked_at_label']}")
    finally:
        session.query(Bond).filter_by(ticker=entry.ticker).delete()
        session.query(Issuer).filter_by(code="LIVETEST").delete()
        session.commit()


async def test_agent_walks_tabs_and_price_views_like_a_user():
    """The full "look at it like a person" flow, against the live page."""
    from app.browser.agent import KaseBrowsingContext

    async with KaseBrowsingContext(label="live-analysis") as agent:
        result = await agent.open_bond(
            "BRKZb14", max_tabs=6, with_screenshot=False, use_cache=False
        )

    assert result.identity_confirmed is True
    assert result.status == "ok"

    # The instrument page's two real sections.
    names = {t.tab_name for t in result.tabs_read}
    assert any("арактеристик" in n for n in names), names
    assert any("орги" in n for n in names), names

    # The toggles a user clicks to switch what the trade table shows.
    views = {t.view for t in result.views_read if t.view}
    assert views == {"clean_price", "dirty_price", "yield"}, views

    accepted = result.validation.accepted if result.validation else {}
    assert len(accepted) > 10, sorted(accepted)


async def test_page_isin_belongs_to_this_bond_not_a_sibling():
    """The page's ISIN must match the exchange's own API for the same bond."""
    from app.browser.agent import KaseBrowsingContext
    from app.providers.kase_public_api import KasePublicApiProvider

    async with KaseBrowsingContext(label="live-isin") as agent:
        result = await agent.open_bond(
            "BRKZb14", max_tabs=2, with_screenshot=False, with_views=False, use_cache=False
        )
    accepted = result.validation.accepted if result.validation else {}
    from_page = accepted["isin"].normalized

    provider = KasePublicApiProvider()
    try:
        from_api = (await provider.get_bond("BRKZb14")).isin
    finally:
        await provider.aclose()

    # Two independent readings of the same fact must agree.
    assert from_page == from_api, f"page={from_page} api={from_api}"


async def test_a_catalog_route_is_refused_even_though_it_answers_200():
    """kase.kz is a SPA: a matching URL is not proof of the right page."""
    from app.browser.agent import KaseBrowsingContext

    async with KaseBrowsingContext(label="live-identity") as agent:
        result = await agent.open_bond(
            "BRKZb14",
            url="https://kase.kz/ru/investors/instruments/BRKZb14",
            max_tabs=1, with_screenshot=False, with_views=False, use_cache=False,
        )
    assert result.identity_confirmed is False
    assert result.status == "not_found"
    assert "does not identify itself" in (result.error or "")


async def test_analysis_reads_the_page_and_compares_with_the_database(session):
    """End to end: browse like a user, then produce findings."""
    from app.services.browser_agent_service import BrowserAgentService

    out = await BrowserAgentService(session).analyze_bond(
        "BRKZb14", max_tabs=6, with_views=True, use_ai=False, persist=False
    )
    analysis = out["analysis"]

    assert analysis["identity_confirmed"] is True
    assert analysis["fields_extracted"] > 10
    assert set(analysis["views_read"]) == {"clean_price", "dirty_price", "yield"}

    # The three views are three descriptions of one quote, not three quotes.
    views = analysis["price_views"]
    assert set(views) == {"clean_price", "dirty_price", "yield"}

    # Without a model the findings are unchanged and the text is deterministic.
    assert out["generated_by"] == "engine"
    assert out["summary"] == out["deterministic_summary"]
    assert "BRKZb14" in out["summary"]
