"""Application configuration.

All configuration is environment driven. Nothing that influences whether real or
mock market data is served may be hardcoded anywhere else in the codebase.
"""

from __future__ import annotations

from functools import lru_cache
import os
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production", "test"]
KaseDataMode = Literal[
    "auto",
    # The public JSON API kase.kz serves to its own front end. Verified, no key
    # required, and the source the product is meant to run on.
    "public_api",
    # Serve the last verified data and never contact KASE at all.
    "offline",
    "official_api",
    # "website_structured" is the spec's name for the plain-HTTP HTML reader.
    # "website" is kept as the historical alias for the same provider.
    "website",
    "website_structured",
    "browser",
    "mock",
]


def _on_vercel() -> bool:
    return os.getenv("VERCEL", "").strip() == "1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        # Deployment dashboards often contain optional variables with an
        # empty value. Treat those as unset so typed defaults remain valid
        # instead of crashing the serverless function during import.
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    # --- application -----------------------------------------------------
    APP_ENV: AppEnv = Field(
        default_factory=lambda: "production" if _on_vercel() else "development"
    )
    APP_NAME: str = "KASE Bond AI"
    API_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = Field(
        default_factory=lambda: (
            "https://nexora-green-xi.vercel.app"
            if _on_vercel()
            else "http://localhost:3000"
        )
    )

    # --- database --------------------------------------------------------
    DATABASE_URL: str = Field(
        default_factory=lambda: (
            "sqlite:////tmp/nexora.db"
            if _on_vercel()
            else "postgresql+psycopg://kase:kase@localhost:5432/kase_bond_ai"
        )
    )

    # --- optional cache --------------------------------------------------
    # Redis is optional by design: the app degrades to an in-process TTL cache.
    REDIS_URL: str | None = None

    # --- KASE data -------------------------------------------------------
    KASE_DATA_MODE: KaseDataMode = Field(
        default_factory=lambda: "public_api" if _on_vercel() else "mock"
    )
    KASE_API_KEY: str | None = None
    KASE_API_URL: str = "https://api.kase.kz"
    KASE_WEBSITE_URL: str = "https://kase.kz"
    KASE_HTTP_TIMEOUT: float = 15.0
    #: Preferred language of the public site. The browser agent switches to it
    #: with the site's own language control when the page renders another one.
    KASE_LANGUAGE: str = "ru"
    RUN_LIVE_KASE_TESTS: bool = False
    STOCK_MARKET_REFRESH_SECONDS: int = 600

    # --- browser agent ---------------------------------------------------
    # The browser agent reads the *public* site as an ordinary visitor. It
    # never needs KASE_API_KEY and never works around a login or a CAPTCHA.
    BROWSER_ENABLED: bool = Field(default_factory=lambda: not _on_vercel())
    BROWSER_ENGINE: Literal["chromium", "firefox", "webkit"] = "chromium"
    BROWSER_HEADLESS: bool = True
    BROWSER_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    )
    BROWSER_LOCALE: str = "ru-RU"
    BROWSER_VIEWPORT_WIDTH: int = 1440
    BROWSER_VIEWPORT_HEIGHT: int = 1000
    #: Per-action timeout (find/click/wait), milliseconds.
    BROWSER_ACTION_TIMEOUT_MS: int = 15_000
    #: Per-navigation timeout, milliseconds.
    BROWSER_NAV_TIMEOUT_MS: int = 45_000
    #: Politeness: never more than this many pages in flight, and never two
    #: navigations closer together than the pacing interval.
    BROWSER_MAX_CONCURRENCY: int = 2
    BROWSER_MIN_INTERVAL_MS: int = 1_200
    BROWSER_MAX_RETRIES: int = 3
    BROWSER_BACKOFF_BASE_MS: int = 1_500
    #: Hard stops so no extraction loop can run forever.
    BROWSER_MAX_PAGES: int = 20
    BROWSER_MAX_SCROLLS: int = 30
    BROWSER_MAX_ROWS: int = 5_000
    BROWSER_MAX_RUNTIME_S: float = 180.0
    #: Cache TTLs (seconds) per kind of page.
    BROWSER_CACHE_TTL_CATALOG_S: float = 3_600.0
    BROWSER_CACHE_TTL_BOND_S: float = 900.0
    BROWSER_CACHE_TTL_ISSUER_S: float = 21_600.0
    BROWSER_CACHE_TTL_DEFAULT_S: float = 900.0
    #: Artefacts. Screenshots are taken only when visual context is needed.
    BROWSER_ARTIFACT_DIR: str = "./var/browser"
    BROWSER_STORE_SCREENSHOTS: bool = True
    BROWSER_MAX_STORED_SCREENSHOTS: int = 200
    #: Visual analysis costs a vision model call; opt-in and never a source of
    #: precise numbers (see docs/browser-agent.md).
    BROWSER_VISUAL_ANALYSIS_ENABLED: bool = True
    BROWSER_VISION_MODEL: str | None = None
    RUN_LIVE_KASE_BROWSER_TESTS: bool = False
    # Historical alias retained for existing developer environments.
    RUN_LIVE_BROWSER_TESTS: bool = False

    # --- incremental ingestion ------------------------------------------
    INCREMENTAL_ENABLED: bool = Field(default_factory=lambda: not _on_vercel())
    INCREMENTAL_PARSER_VERSION: str = "2.0.0"
    INCREMENTAL_FAST_CHECK_TIMEOUT: float = 12.0
    INCREMENTAL_FORCE_FULL_AFTER_HOURS: float = 168.0
    INCREMENTAL_MISSING_FIELD_RATIO: float = 0.60
    MATERIAL_YTM_ABSOLUTE_CHANGE: float = 0.005
    MATERIAL_PRICE_PERCENT_CHANGE: float = 1.0
    MATERIAL_SPREAD_CHANGE: float = 0.5
    MATERIAL_CREDIT_SCORE_CHANGE: float = 5.0
    MATERIAL_LIQUIDITY_SCORE_CHANGE: float = 5.0
    MATERIAL_TRADE_VOLUME_CHANGE: float = 0.25
    # Market inference receives a new observation at most every ten minutes.
    # Retraining remains a separate evaluated release process.
    SCHEDULE_QUOTES_SECONDS: int = 600
    SCHEDULE_CATALOG_SECONDS: int = 21_600
    SCHEDULE_DOCUMENTS_SECONDS: int = 21_600
    SCHEDULE_NEWS_SECONDS: int = 3_600
    NEWS_COLLECTION_ENABLED: bool = Field(default_factory=lambda: not _on_vercel())
    NEWS_MINIMUM_ANALOG_SAMPLE: int = 5
    SCHEDULE_AI_TASKS_SECONDS: int = 60
    SCHEDULE_FORECAST_TRAINING_SECONDS: int = 2_592_000
    FORECAST_MATERIAL_PROBABILITY_CHANGE: float = 0.08
    FORECAST_MATERIAL_EXPECTED_RETURN_CHANGE: float = 0.04
    FORECAST_MATERIAL_INTERVAL_WIDTH_CHANGE: float = 0.06
    FORECAST_MATERIAL_CONFIDENCE_CHANGE: float = 0.15
    SCHEDULE_BOND_TERMS_SECONDS: int = 86_400
    RAW_SNAPSHOT_RETENTION_DAYS: int = 30

    # --- AI --------------------------------------------------------------
    # The product's primary intelligence is our own model, served by the
    # inference service in ai/inference on our own infrastructure. See
    # docs/ai/architecture.md.
    #
    #   local     - KASE Bond AI (default). No external LLM API is involved.
    #   external  - an OpenAI-compatible endpoint. Opt-in, for evaluation and
    #               comparison only; it is not a fallback the system reaches
    #               for on its own (§61).
    #   off       - no model at all; every AI surface serves its deterministic
    #               explanation instead.
    AI_PROVIDER: str = "local"
    AI_ENABLED: bool = Field(default_factory=lambda: not _on_vercel())
    AI_TIMEOUT: float = 30.0
    AI_MAX_TOKENS: int = 900

    #: Our inference service (ai/inference/server.py).
    KASE_AI_URL: str = "http://127.0.0.1:8100"
    KASE_AI_MODEL_VERSION: str = "kase-ai-v0.1"
    KASE_AI_DATA_MODE: Literal["snapshot", "live"] = "snapshot"
    KASE_AI_LIVE_TTL_SECONDS: float = 300.0

    # Used only when AI_PROVIDER=external.
    OPENAI_API_KEY: str | None = None
    AI_BASE_URL: str = "https://api.openai.com/v1"
    AI_MODEL: str = "gpt-4o-mini"

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
    def is_serverless(self) -> bool:
        return _on_vercel()

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
        if self.KASE_DATA_MODE == "browser" and not self.BROWSER_ENABLED:
            problems.append(
                "KASE_DATA_MODE=browser requires BROWSER_ENABLED=true."
            )
        return problems

    @property
    def browser_limits(self) -> dict[str, float]:
        return {
            "max_pages": self.BROWSER_MAX_PAGES,
            "max_scrolls": self.BROWSER_MAX_SCROLLS,
            "max_rows": self.BROWSER_MAX_ROWS,
            "max_runtime_s": self.BROWSER_MAX_RUNTIME_S,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
