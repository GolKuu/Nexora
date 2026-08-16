"""End-to-end API tests against the demo dataset."""

from __future__ import annotations

import uuid

import pytest

TICKER = "DBNKb1"


def test_health_reports_environment_and_database(api):
    body = api.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert body["database"]["bonds"] > 0


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_development_cors_accepts_both_loopback_names(client, host):
    origin = f"http://{host}:3000"
    response = client.get("/api/v1/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_health_kase_admits_demo_data(api):
    body = api.get("/health/kase").json()
    assert body["is_mock"] is True
    # Never claim a connection we do not have.
    assert body["connected"] is False
    assert "демонстрационные" in body["warning"].lower()


def test_list_bonds_is_public_and_flags_mock_data(api):
    body = api.get("/bonds?limit=5").json()
    assert body["total"] > 0
    assert len(body["items"]) == 5
    assert body["warning"] is not None
    assert all(item["data_mode"] == "mock" for item in body["items"])


def test_top_is_sorted_by_investment_score(api):
    items = api.get("/bonds/top?limit=5").json()["items"]
    assert items
    scores = [i["investment_score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 100 for s in scores)


def test_top_can_be_filtered_by_category(api):
    items = api.get("/bonds/top?limit=10&category=government").json()["items"]
    assert items
    assert all(i["bond_type"] == "government" for i in items)


def test_search_matches_ticker_and_name(api):
    assert api.get("/bonds/search?q=DBNK").json()["items"]
    assert api.get("/bonds/search?q=демо").json()["total"] >= 0


def test_search_rejects_an_empty_query(api):
    assert api.get("/bonds/search?q=").status_code == 422


def test_bond_card_has_both_simple_and_pro_payloads(api):
    body = api.get(f"/bonds/{TICKER}").json()
    simple = body["simple"]
    assert set(simple) >= {
        "yield_pct", "real_yield_pct", "years_to_maturity",
        "reliability", "liquidity", "growth_potential", "overall",
    }
    assert simple["overall"]["verdict"]
    assert body["pro"]["available"] is True
    assert body["pro"]["modified_duration"] is not None
    assert body["freshness"]["is_mock"] is True


def test_simple_view_hides_technical_terms(api):
    simple = api.get(f"/bonds/{TICKER}").json()["simple"]
    for technical in ("ytm", "duration", "convexity", "credit_spread", "dirty_price"):
        assert technical not in simple


def test_bond_lookup_by_isin_and_id(api):
    by_ticker = api.get(f"/bonds/{TICKER}").json()["bond"]
    assert api.get(f"/bonds/{by_ticker['isin']}").json()["bond"]["ticker"] == TICKER
    assert api.get(f"/bonds/{by_ticker['id']}").json()["bond"]["ticker"] == TICKER


def test_unknown_bond_returns_a_structured_404(api):
    response = api.get("/bonds/NOSUCHBOND")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_metrics_endpoint_exposes_provenance(api):
    body = api.get(f"/bonds/{TICKER}/metrics").json()
    assert body["metrics"]["formula_version"]
    assert body["freshness"]["data_mode"] == "mock"


def test_scores_endpoint_returns_components_and_weights(api):
    body = api.get(f"/bonds/{TICKER}/scores").json()
    investment = body["scores"]["investment"]
    assert 0 <= investment["value"] <= 100
    assert investment["components"]
    total_weight = sum(c["weight"] for c in investment["components"])
    assert total_weight == pytest.approx(1.0, abs=1e-6)


def test_score_explanation_falls_back_to_the_engine_without_an_llm(api):
    body = api.get(f"/bonds/{TICKER}/score-explanation?use_ai=false").json()
    assert body["generated_by"] == "engine"
    assert body["text"]
    assert body["explanation"]["components"]


def test_score_explanation_with_ai_disabled_still_answers(api):
    body = api.get(f"/bonds/{TICKER}/score-explanation").json()
    # AI_ENABLED=false in the test env, so the deterministic text is served.
    assert body["generated_by"] == "engine"
    assert body["text"]


def test_cashflows_are_ordered_and_end_with_principal(api):
    rows = api.get(f"/bonds/{TICKER}/cashflows").json()
    assert rows
    dates = [r["payment_date"] for r in rows]
    assert dates == sorted(dates)
    assert rows[-1]["principal_amount"] > 0
    assert all(r["principal_amount"] == 0 for r in rows[:-1])


def test_history_returns_points(api):
    rows = api.get(f"/bonds/{TICKER}/history?days=365").json()
    assert isinstance(rows, list)


def test_peers_are_comparable_issues(api):
    body = api.get(f"/bonds/{TICKER}/peers").json()
    assert body["peer_group"]
    assert TICKER not in [p["ticker"] for p in body["peers"]]


# -- calculator --------------------------------------------------------------

def test_calculator_projects_a_real_investment(api):
    body = api.post(f"/bonds/{TICKER}/calculate", json={"amount": 1_000_000}).json()
    assert body["available"] is True
    assert body["quantity"] >= 1
    assert body["invested"] <= 1_000_000
    assert body["proceeds"] > 0
    assert body["schedule"]
    assert body["assumptions"]


def test_calculator_reports_when_the_amount_is_too_small(api):
    body = api.post(f"/bonds/{TICKER}/calculate", json={"amount": 10}).json()
    assert body["available"] is False
    assert "недостаточно" in body["reason"].lower()


def test_calculator_rejects_a_non_positive_amount(api):
    assert api.post(f"/bonds/{TICKER}/calculate", json={"amount": 0}).status_code == 422
    assert api.post(f"/bonds/{TICKER}/calculate", json={"amount": -5}).status_code == 422


def test_calculator_real_return_is_below_nominal_under_inflation(api):
    body = api.post(f"/bonds/{TICKER}/calculate", json={"amount": 5_000_000}).json()
    assert body["inflation_pct"] is not None
    assert body["real_total_return_pct"] < body["total_return_pct"]


# -- compare -----------------------------------------------------------------

def test_compare_returns_rows_columns_and_a_winner(api):
    body = api.post(
        "/compare", json={"identifiers": [TICKER, "MOM072_2510"], "mode": "simple"}
    ).json()
    assert len(body["columns"]) == 2
    assert body["rows"]
    assert body["winner"]["investment_score"] is not None


def test_compare_pro_mode_adds_technical_rows(api):
    simple = api.post("/compare", json={"identifiers": [TICKER], "mode": "simple"}).json()
    pro = api.post("/compare", json={"identifiers": [TICKER], "mode": "pro"}).json()
    assert len(pro["rows"]) > len(simple["rows"])
    assert any(r["key"] == "modified_duration" for r in pro["rows"])


def test_compare_limits_the_number_of_issues(api):
    response = api.post("/compare", json={"identifiers": ["a"] * 6})
    assert response.status_code == 422


def test_compare_requires_at_least_one_issue(api):
    assert api.post("/compare", json={"identifiers": []}).status_code == 422


# -- settings ----------------------------------------------------------------

def test_settings_defaults_are_simple_mode_with_inflation_on(api):
    body = api.get("/settings").json()
    assert body["ui_mode"] == "simple"
    assert body["inflation_enabled"] is True
    assert body["show_real_return"] is True
    assert body["base_currency"] == "KZT"
    assert body["persisted"] is False


def test_settings_persist_for_an_anonymous_token(api):
    token = uuid.uuid4().hex
    headers = {"X-Anon-Token": token}
    updated = api.put(
        "/settings", json={"ui_mode": "pro", "risk_profile": "aggressive"}, headers=headers
    ).json()
    assert updated["ui_mode"] == "pro"
    assert api.get("/settings", headers=headers).json()["ui_mode"] == "pro"
    # A different visitor is unaffected.
    assert api.get("/settings").json()["ui_mode"] == "simple"


def test_settings_reject_an_unknown_value(api):
    response = api.put(
        "/settings", json={"ui_mode": "expert"}, headers={"X-Anon-Token": uuid.uuid4().hex}
    )
    assert response.status_code == 422


def test_manual_inflation_requires_a_rate(api):
    response = api.put(
        "/settings",
        json={"inflation_source": "manual"},
        headers={"X-Anon-Token": uuid.uuid4().hex},
    )
    assert response.status_code == 422


def test_saving_settings_without_an_identity_is_rejected(api):
    assert api.put("/settings", json={"ui_mode": "pro"}).status_code == 422


def test_effective_inflation_reports_its_source(api):
    body = api.get("/settings/inflation?horizon_years=3").json()
    assert body["enabled"] is True
    assert body["rate"] is not None
    assert body["kind"] in ("official", "forecast", "manual")


# -- watchlist and portfolio -------------------------------------------------

def test_watchlist_requires_an_identity_to_write_but_not_to_read(api):
    assert api.get("/watchlist").json()["requires_identity"] is True
    assert api.post("/watchlist", json={"bond": TICKER}).status_code == 422


def test_watchlist_add_list_and_remove(api):
    headers = {"X-Anon-Token": uuid.uuid4().hex}
    created = api.post("/watchlist", json={"bond": TICKER}, headers=headers).json()
    assert created["ticker"] == TICKER
    items = api.get("/watchlist", headers=headers).json()["items"]
    assert [i["ticker"] for i in items] == [TICKER]
    assert api.delete(f"/watchlist/{TICKER}", headers=headers).status_code == 204
    assert api.get("/watchlist", headers=headers).json()["items"] == []


def test_watchlist_is_idempotent(api):
    headers = {"X-Anon-Token": uuid.uuid4().hex}
    api.post("/watchlist", json={"bond": TICKER}, headers=headers)
    second = api.post("/watchlist", json={"bond": TICKER}, headers=headers).json()
    assert second["already_present"] is True


def test_portfolio_lifecycle(api):
    headers = {"X-Anon-Token": uuid.uuid4().hex}
    portfolio_id = api.post(
        "/portfolios", json={"name": "Тест"}, headers=headers
    ).json()["id"]

    position = api.post(
        f"/portfolios/{portfolio_id}/positions",
        json={"bond": TICKER, "quantity": 100, "purchase_clean_price": 98.5},
        headers=headers,
    ).json()

    body = api.get(f"/portfolios/{portfolio_id}", headers=headers).json()
    assert body["summary"]["position_count"] == 1
    assert body["summary"]["market_value"] > 0
    assert body["summary"]["portfolio_ytm_pct"] is not None

    api.put(
        f"/portfolios/{portfolio_id}/positions/{position['id']}",
        json={"quantity": 200},
        headers=headers,
    )
    body = api.get(f"/portfolios/{portfolio_id}", headers=headers).json()
    assert body["positions"][0]["quantity"] == 200

    assert api.delete(
        f"/portfolios/{portfolio_id}/positions/{position['id']}", headers=headers
    ).status_code == 204


def test_a_portfolio_is_invisible_to_other_visitors(api):
    owner = {"X-Anon-Token": uuid.uuid4().hex}
    stranger = {"X-Anon-Token": uuid.uuid4().hex}
    portfolio_id = api.post("/portfolios", json={"name": "Личный"}, headers=owner).json()["id"]
    assert api.get(f"/portfolios/{portfolio_id}", headers=stranger).status_code == 404
    assert api.get("/portfolios", headers=stranger).json()["items"] == []


# -- meta --------------------------------------------------------------------

def test_scoring_model_weights_come_from_the_backend(api):
    body = api.get("/meta/scoring-model?profile=conservative").json()
    assert body["profile"] == "conservative"
    assert sum(body["weights"]["investment"].values()) == pytest.approx(1.0)
    assert body["labels"]["credit_quality"]


def test_categories_are_published_for_the_home_page(api):
    body = api.get("/meta/categories").json()
    assert {c["code"] for c in body["categories"]} >= {"government", "corporate", "bank"}


def test_sources_endpoint_admits_the_demo_source(api):
    body = api.get("/meta/sources").json()
    assert body["configured_mode"] == "mock"
    assert any(s["kind"] == "mock" for s in body["sources"])
