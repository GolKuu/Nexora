"""Provider contract and the production mock guard."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.enums import DataMode
from app.core.errors import ConfigurationError, MockDataForbiddenError
from app.providers.base import BondDataProvider
from app.providers.factory import build_provider
from app.providers.mock_kase import MockKaseProvider

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_mock_provider_labels_everything_as_mock():
    provider = MockKaseProvider()
    assert provider.is_mock is True
    assert provider.data_mode == DataMode.MOCK.value

    bonds = await provider.get_bonds()
    assert bonds
    assert all(b.provenance.data_mode == DataMode.MOCK.value for b in bonds)
    assert all(b.provenance.source == "mock" for b in bonds)

    quotes = await provider.get_quotes()
    assert quotes
    assert all(q.provenance.data_mode == DataMode.MOCK.value for q in quotes)


async def test_mock_health_states_that_kase_is_not_connected():
    status = await MockKaseProvider().health()
    assert status.is_mock is True
    assert "KASE" in status.detail and "НЕ подключен" in status.detail


async def test_mock_provider_is_deterministic():
    first = await MockKaseProvider().get_quotes(["DBNKb1"])
    second = await MockKaseProvider().get_quotes(["DBNKb1"])
    assert first[0].clean_price == second[0].clean_price
    assert first[0].ytm == second[0].ytm


async def test_mock_lookup_by_ticker_and_isin():
    provider = MockKaseProvider()
    by_ticker = await provider.get_bond("DBNKb1")
    by_isin = await provider.get_bond(by_ticker.isin)
    assert by_ticker.ticker == by_isin.ticker
    assert await provider.get_bond("NOPE") is None


async def test_search_matches_ticker_and_name():
    provider = MockKaseProvider()
    assert await provider.search_bonds("DBNK")
    assert await provider.search_bonds("демобанк") or await provider.search_bonds("ДемоБанк")


async def test_bank_issuer_is_flagged_for_the_bank_credit_model():
    issuer = await MockKaseProvider().get_issuer("DEMOBANK")
    assert issuer.is_financial_institution is True
    assert issuer.sector == "bank"


def test_every_abstract_method_is_implemented():
    required = {
        "get_bonds", "get_bond", "search_bonds", "get_quotes", "get_trades",
        "get_issuer", "get_financials", "get_documents", "get_ratings",
    }
    assert required <= set(BondDataProvider.__abstractmethods__)
    provider = MockKaseProvider()
    assert all(callable(getattr(provider, name)) for name in required)


# -- factory guards ----------------------------------------------------------

def test_production_refuses_mock_mode():
    config = Settings(APP_ENV="production", KASE_DATA_MODE="mock")
    with pytest.raises(MockDataForbiddenError):
        build_provider(config)


def test_production_auto_mode_excludes_the_mock_fallback():
    config = Settings(APP_ENV="production", KASE_DATA_MODE="auto", KASE_API_KEY=None)
    provider = build_provider(config)
    assert all(not p.is_mock for p in provider.providers)


def test_development_auto_mode_includes_a_labelled_mock_fallback():
    config = Settings(APP_ENV="development", KASE_DATA_MODE="auto", KASE_API_KEY=None)
    provider = build_provider(config)
    assert any(p.is_mock for p in provider.providers)
    # The fallback is last: real sources are always tried first.
    assert provider.providers[-1].is_mock


def test_official_api_mode_requires_a_key():
    config = Settings(APP_ENV="development", KASE_DATA_MODE="official_api", KASE_API_KEY=None)
    with pytest.raises(ConfigurationError):
        build_provider(config)


def test_config_validation_flags_mock_in_production():
    problems = Settings(APP_ENV="production", KASE_DATA_MODE="mock").validate_runtime()
    assert problems and "mock" in problems[0].lower()
    assert Settings(APP_ENV="development", KASE_DATA_MODE="mock").validate_runtime() == []


def test_empty_deployment_variables_fall_back_to_typed_defaults(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "")
    monkeypatch.setenv("AI_TIMEOUT", "")
    monkeypatch.setenv("SCHEDULE_QUOTES_SECONDS", "")
    monkeypatch.setenv("KASE_AI_DATA_MODE", "")

    config = Settings(_env_file=None)

    assert config.AI_ENABLED is True
    assert config.AI_TIMEOUT == 30.0
    assert config.SCHEDULE_QUOTES_SECONDS == 900
    assert config.KASE_AI_DATA_MODE == "snapshot"


def test_vercel_defaults_are_real_serverless_and_need_no_paid_database(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    for name in (
        "APP_ENV", "DATABASE_URL", "KASE_DATA_MODE", "BROWSER_ENABLED",
        "INCREMENTAL_ENABLED", "AI_ENABLED",
    ):
        monkeypatch.setenv(name, "")

    config = Settings(_env_file=None)

    assert config.APP_ENV == "production"
    assert config.KASE_DATA_MODE == "public_api"
    assert config.DATABASE_URL == "sqlite:////tmp/nexora.db"
    assert config.BROWSER_ENABLED is False
    assert config.INCREMENTAL_ENABLED is False
    assert config.AI_ENABLED is False
    assert config.is_serverless is True
    assert config.validate_runtime() == []
