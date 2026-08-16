"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    problems = settings.validate_runtime()
    if problems:
        # Refuse to start rather than quietly serve the wrong kind of data.
        for problem in problems:
            logger.error("configuration error: %s", problem)
        raise RuntimeError("; ".join(problems))
    logger.info(
        "starting %s env=%s kase_mode=%s",
        settings.APP_NAME,
        settings.APP_ENV,
        settings.KASE_DATA_MODE,
    )
    if settings.KASE_DATA_MODE == "mock":
        logger.warning(
            "SERVING DEMO DATA: KASE is not connected and every market figure "
            "is synthetic."
        )
    periodic = None
    if settings.INCREMENTAL_ENABLED and settings.APP_ENV != "test" and settings.KASE_DATA_MODE != "mock":
        from app.jobs.scheduler import PeriodicRefresh

        periodic = PeriodicRefresh()
        periodic.start()
    yield
    if periodic is not None:
        await periodic.stop()
    # The browser engine is a child process; leaving it behind leaks memory.
    from app.browser.session import browser_service

    await browser_service.aclose()
    logger.info("shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description=(
        "Раздельный анализ облигаций и акций KASE. Простые понятия для пользователя, "
        "детерминированный расчетный движок внутри. "
        "LLM объясняет, но никогда не считает."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Developers commonly open the local UI through either loopback name.
    # Keep this convenience development-only; production remains restricted
    # to the explicit CORS_ORIGINS allow-list.
    allow_origin_regex=(
        r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$"
        if not settings.is_production
        else None
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)
app.include_router(api_router, prefix=settings.API_PREFIX)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "name": settings.APP_NAME,
        "api": settings.API_PREFIX,
        "docs": "/docs",
        "kase_data_mode": settings.KASE_DATA_MODE,
    }
