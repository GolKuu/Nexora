"""The strict scoring API must explain every number it returns."""

from __future__ import annotations

STRONG_BOND = {
    "ticker": "APITEST1",
    "currency": "KZT",
    "bond_type": "corporate",
    "coupon_rate": 0.14,
    "coupon_type": "fixed",
    "coupon_frequency": 2,
    "years_to_maturity": 4.0,
    "modified_duration": 3.2,
    "ytm": 0.145,
    "secured": False,
    "subordinated": False,
    "callable": False,
    "covenants": "standard",
    "market": {
        "price": 98.4, "bid": 98.2, "ask": 98.6, "avg_daily_turnover": 8e7,
        "trade_count_30d": 18, "days_since_last_trade": 1.0, "order_book_depth": 2e7,
        "provenance": {"source": "kase.kz", "published_at": "2026-06-30T00:00:00Z",
                       "official": True},
    },
    "financials": {
        "revenue": 210e9, "ebitda": 60e9, "net_income": 25e9, "interest_expense": 9e9,
        "total_debt": 120e9, "cash": 36e9, "short_term_debt": 22e9, "equity": 170e9,
        "operating_cash_flow": 38e9, "capex": 16e9,
        "net_debt_to_ebitda": 1.4, "interest_coverage": 6.7, "debt_to_equity": 0.7,
        "debt_change_1y": -0.05,
        "provenance": {"source": "kase.kz", "as_of": "2026-03-31T00:00:00Z",
                       "published_at": "2026-05-15T00:00:00Z", "official": True},
    },
    "events": {"rating": "BBB", "rating_outlook": "stable"},
    "macro": {"inflation_rate": 0.095, "benchmark_yield": 0.125, "rate_outlook": "stable"},
    "peers": {"peer_count": 6, "peer_median_ytm": 0.138},
    "meta": {"official_source_ratio": 0.9, "parser_confidence": 0.95, "history_years": 6.0,
             "data_mode": "live"},
}

WEAK_BOND = {
    **STRONG_BOND,
    "ticker": "APITEST2",
    "ytm": 0.30,
    "coupon_rate": 0.28,
    "financials": {
        "ebitda": 6.2e9, "interest_expense": 5.2e9, "total_debt": 44e9, "cash": 1.1e9,
        "short_term_debt": 9e9, "equity": 8e9, "operating_cash_flow": 3.4e9, "capex": 8.5e9,
        "net_debt_to_ebitda": 6.5, "interest_coverage": 1.2, "debt_to_equity": 3.5,
        "debt_change_1y": 0.30,
        "provenance": {"source": "kase.kz", "published_at": "2026-05-15T00:00:00Z",
                       "official": True},
    },
    "events": {"rating": "CCC"},
}


def test_model_endpoint_publishes_weights_caps_and_versions(api):
    body = api.get("/scoring/model").json()

    assert body["versions"]["bond"]["model"] == "bond_score_v1"
    assert sum(body["weights"]["bond"].values()) == 1.0
    assert sum(body["weights"]["stock"].values()) == 1.0
    assert sum(body["weights"]["bank"].values()) == 1.0
    assert any(cap["code"] == "CREDIT_BELOW_30" for cap in body["caps"]["bond"])
    assert body["rules"]["missing_data_prior"] > 0


def test_bond_endpoint_explains_the_score(api):
    body = api.post("/scoring/bond", json=STRONG_BOND).json()

    assert body["final_score"] >= 75
    assert body["summary"]
    assert body["strengths"]
    assert body["score"]["version"]["model"] == "bond_score_v1"
    assert {c["code"] for c in body["components"]} >= {"credit_quality", "yield_quality"}
    for component in body["components"]:
        assert "weight" in component and "raw_value" in component
    assert body["ignored_fields"] == []
    assert body["facts_fingerprint"]


def test_high_yield_weak_credit_is_explained_not_rewarded(api):
    strong = api.post("/scoring/bond", json=STRONG_BOND).json()
    weak = api.post("/scoring/bond", json=WEAK_BOND).json()

    assert weak["final_score"] < strong["final_score"]
    assert weak["final_score"] <= 45
    assert weak["red_flags"]
    assert weak["all_caps"]


def test_unknown_fields_are_reported_not_swallowed(api):
    payload = {**STRONG_BOND, "ticker": "APITEST3", "net_debt_to_ebtida": 1.4}
    body = api.post("/scoring/bond", json=payload).json()

    assert "net_debt_to_ebtida" in body["ignored_fields"]
    assert any("проигнорированы" in note for note in body["data_limitations"])


def test_bad_input_is_rejected_with_a_message(api, client):
    response = client.post(
        "/api/v1/scoring/bond", json={"ticker": "X", "ytm": "не число"}
    )
    assert response.status_code >= 400


def test_point_in_time_is_honoured_by_the_api(api):
    early = api.post(
        "/scoring/bond", json={**STRONG_BOND, "ticker": "APITEST4"},
        params={"as_of": "2026-05-01"},
    ).json()
    late = api.post(
        "/scoring/bond", json={**STRONG_BOND, "ticker": "APITEST4"},
        params={"as_of": "2026-07-01"},
    ).json()

    assert early["score"]["excluded_facts"], early
    assert not late["score"]["excluded_facts"]
    assert early["final_score"] < late["final_score"]


def test_snapshots_are_append_only(api):
    payload = {**STRONG_BOND, "ticker": "APITEST5"}
    first = api.post("/scoring/bond", json=payload, params={"persist": True}).json()
    again = api.post("/scoring/bond", json=payload, params={"persist": True}).json()

    # Identical facts and model version: the same row, not a duplicate.
    assert first["snapshot_id"] == again["snapshot_id"]

    changed = api.post(
        "/scoring/bond",
        json={**payload, "ytm": 0.16},
        params={"persist": True},
    ).json()
    assert changed["snapshot_id"] != first["snapshot_id"]

    history = api.get("/scoring/history/APITEST5").json()
    assert history["count"] == 2
    assert {s["model_version"] for s in history["snapshots"]} == {"bond_score_v1"}

    stored = api.get(f"/scoring/snapshot/{first['snapshot_id']}").json()
    assert stored["breakdown"]["components"]
    assert stored["facts_fingerprint"] == first["facts_fingerprint"]


def test_bank_endpoint_uses_the_bank_model(api):
    body = api.post(
        "/scoring/bank",
        json={
            "ticker": "APIBANK",
            "pe": 6.5, "pb": 1.1, "dividend_yield": 0.07,
            "market": {"price": 190.0, "bid": 189.0, "ask": 191.0,
                       "avg_daily_turnover": 9e7, "trade_count_30d": 21,
                       "days_since_last_trade": 1.0, "free_float_pct": 0.3},
            "bank_financials": {
                "roe": 0.22, "roa": 0.028, "net_interest_margin": 0.05,
                "capital_adequacy_ratio": 0.18, "tier1_ratio": 0.15,
                "equity_to_assets": 0.13, "npl_ratio": 0.035, "npl_coverage": 1.1,
                "cost_of_risk": 0.008, "loan_to_deposit": 0.85, "deposit_growth": 0.12,
                "liquid_assets_ratio": 0.25, "cost_to_income": 0.42, "equity": 1.4e12,
                "provenance": {"source": "kase.kz", "published_at": "2026-05-15T00:00:00Z",
                               "official": True},
            },
            "meta": {"official_source_ratio": 0.9, "parser_confidence": 0.95,
                     "history_years": 6.0, "data_mode": "live"},
        },
    ).json()

    assert body["score"]["kind"] == "bank"
    assert body["version"]["model"] == "bank_score_v1"
    codes = {c["code"] for c in body["components"]}
    assert "capital_strength" in codes and "leverage" not in codes
