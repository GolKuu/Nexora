"""Regression guards for fast, stored-data-only client reads."""

from __future__ import annotations

import pytest

from app.collectors.kase_stock_catalog import KaseStockCatalogCollector


def test_top_stocks_never_collects_kase_in_request_path(client, monkeypatch):
    async def forbidden_collect(*_args, **_kwargs):
        pytest.fail("GET /stocks/top attempted synchronous KASE collection")

    monkeypatch.setattr(KaseStockCatalogCollector, "collect", forbidden_collect)

    response = client.get("/api/v1/stocks/top?limit=5")

    assert response.status_code == 200
    refresh = response.json()["market_refresh"]
    assert refresh["refreshed"] is False
    assert refresh["refresh_mode"] == "background_or_manual"


def test_stock_market_status_is_database_only(session, monkeypatch):
    async def forbidden_collect(*_args, **_kwargs):
        pytest.fail("freshness metadata attempted synchronous KASE collection")

    monkeypatch.setattr(KaseStockCatalogCollector, "collect", forbidden_collect)

    from app.services.stock_market import stored_stock_market_status

    status = stored_stock_market_status(session)

    assert status["refreshed"] is False
    assert status["status"] in {"stored", "not_collected"}
