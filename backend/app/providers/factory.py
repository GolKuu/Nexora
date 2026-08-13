"""Provider selection - the single place KASE_DATA_MODE is interpreted."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, settings
from app.core.errors import ConfigurationError, MockDataForbiddenError
from app.core.logging import get_logger
from app.providers.base import BondDataProvider
from app.providers.composite import CompositeKaseProvider
from app.providers.kase_api import KaseApiProvider
from app.providers.kase_browser import KaseBrowserProvider
from app.providers.kase_website import KaseWebsiteProvider
from app.providers.mock_kase import MockKaseProvider

logger = get_logger(__name__)

#: Historical alias kept working: "website" == "website_structured".
_STRUCTURED_MODES = {"website", "website_structured"}


def build_provider(config: Settings | None = None) -> BondDataProvider:
    """Construct the provider chain described by the configuration.

    ``auto``                official API (if a key exists) -> browser agent ->
                            plain HTML reader -> mock (dev only)
    ``official_api``        official API only
    ``website_structured``  plain HTTP reader of the public HTML (alias: website)
    ``browser``             real browser session on the public site only
    ``mock``                demo data, refused in production

    Note what is *not* here: no mode requires KASE_API_KEY except
    ``official_api``. The public site is public (§2).
    """
    config = config or settings
    mode = config.KASE_DATA_MODE

    if mode == "mock":
        if config.is_production:
            raise MockDataForbiddenError(
                "KASE_DATA_MODE=mock is not allowed with APP_ENV=production."
            )
        logger.warning("KASE data mode: MOCK. All market data is synthetic.")
        return MockKaseProvider()

    if mode == "official_api":
        if not config.KASE_API_KEY:
            raise ConfigurationError(
                "KASE_DATA_MODE=official_api requires KASE_API_KEY."
            )
        return KaseApiProvider(config.KASE_API_URL, config.KASE_API_KEY)

    if mode in _STRUCTURED_MODES:
        return KaseWebsiteProvider(config.KASE_WEBSITE_URL)

    if mode == "browser":
        if not config.BROWSER_ENABLED:
            raise ConfigurationError(
                "KASE_DATA_MODE=browser requires BROWSER_ENABLED=true."
            )
        # In browser mode the data really is fetched by a browser: no silent
        # fallback to the HTML reader, and none to mock (§47).
        logger.info("KASE data mode: BROWSER. Data is read from the public site.")
        return KaseBrowserProvider(config.KASE_WEBSITE_URL)

    # auto (§48): structured official source, then the browser agent, then the
    # plain HTML reader; the last-verified cache lives in the database and is
    # what the API serves when every live source fails.
    chain: list[BondDataProvider] = []
    if config.KASE_API_KEY:
        chain.append(KaseApiProvider(config.KASE_API_URL, config.KASE_API_KEY))
    if config.BROWSER_ENABLED:
        chain.append(KaseBrowserProvider(config.KASE_WEBSITE_URL))
    chain.append(KaseWebsiteProvider(config.KASE_WEBSITE_URL))
    if config.mock_allowed:
        logger.warning(
            "KASE data mode: AUTO with mock fallback enabled (APP_ENV=%s). "
            "Any mock answer stays labelled data_mode=mock.",
            config.APP_ENV,
        )
        chain.append(MockKaseProvider())
    return CompositeKaseProvider(chain)


@lru_cache
def get_provider() -> BondDataProvider:
    return build_provider()
