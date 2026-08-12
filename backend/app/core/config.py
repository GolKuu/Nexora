"""Application configuration.

All configuration is environment driven. Nothing that influences whether real or
mock market data is served may be hardcoded anywhere else in the codebase.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production", "test"]
KaseDataMode = Literal["auto", "official_api", "website", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application -----------------------------------------------------
    APP_ENV: AppEnv = "development"
    APP_NAME: str = "KASE Bond AI"
    API_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:3000"

    # --- database --------------------------------------------------------
    DATABASE_URL: str = "postgresql+psycopg://kase:kase@localhost:5432/kase_bond_ai"

    # --- optional cache --------------------------------------------------
    # Redis is optional by design: the app degrades to an in-process TTL cache.
    REDIS_URL: str | None = None

    # --- KASE data -------------------------------------------------------
    KASE_DATA_MODE: KaseDataMode = "mock"
    KASE_API_KEY: str | None = None
    KASE_API_URL: str = "https://api.kase.kz"
    KASE_WEBSITE_URL: str = "https://kase.kz"
    KASE_HTTP_TIMEOUT: float = 15.0
    RUN_LIVE_KASE_TESTS: bool = False

    # --- AI --------------------------------------------------------------
    # OpenAI-compatible: works with OpenAI, Claude-compatible gateways, Qwen, etc.
    OPENAI_API_KEY: str | None = None
    AI_BASE_URL: str = "https://api.openai.com/v1"
    AI_MODEL: str = "gpt-4o-mini"
    AI_ENABLED: bool = True
    AI_TIMEOUT: float = 30.0
    AI_MAX_TOKENS: int = 900

    # --- scoring ---------------------------------------------------------
    SCORING_MODEL_VERSION: str = "1.0.0"
    FORMULA_VERSION: str = "1.0.0"

    # --- inflation -------------------------------------------------------
    DEFAULT_INFLATION_RATE: float = 0.0
    INFLATION_SOURCE_URL: str = "https://stat.gov.kz"

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def mock_allowed(self) -> bool:
        """Mock data is only ever permitted outside production."""
        return not self.is_production

    def validate_runtime(self) -> list[str]:
        """Return a list of fatal configuration problems (empty == ok)."""
        problems: list[str] = []
        if self.is_production and self.KASE_DATA_MODE == "mock":
            problems.append(
                "KASE_DATA_MODE=mock is forbidden when APP_ENV=production. "
                "Mock data must never be served as production data."
            )
        if self.KASE_DATA_MODE == "official_api" and not self.KASE_API_KEY:
            problems.append(
                "KASE_DATA_MODE=official_api requires KASE_API_KEY to be set."
            )
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
