"""Which model the backend talks to.

Default is ``local``: our own KASE Bond AI service (``ai/inference``). The
external OpenAI-compatible client is still here, but it is opt-in and exists
for one purpose — running the base-model comparison in
``ai/evaluation/compare_models.py``. It is never selected automatically, and
nothing falls back to it when the local service is down (§61): an unreachable
model degrades to the deterministic explanation the scoring engine produces,
which is the behaviour the product has always had.
"""

from __future__ import annotations

from functools import lru_cache

from app.ai.base import LLMClient, NullLLMClient
from app.ai.local_client import KaseLocalClient
from app.ai.openai_compatible import OpenAICompatibleClient
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache
def get_llm_client() -> LLMClient:
    if not settings.AI_ENABLED:
        return NullLLMClient()

    provider = (settings.AI_PROVIDER or "local").strip().lower()

    if provider == "local":
        return KaseLocalClient()

    if provider == "external":
        if not settings.OPENAI_API_KEY:
            logger.warning(
                "AI_PROVIDER=external but OPENAI_API_KEY is empty; AI features degrade to "
                "deterministic explanations."
            )
            return NullLLMClient()
        logger.warning(
            "AI_PROVIDER=external: the product is being served by a third-party model. "
            "This is a comparison mode, not the product's intended configuration."
        )
        return OpenAICompatibleClient()

    if provider in ("off", "none", "null"):
        return NullLLMClient()

    logger.warning("unknown AI_PROVIDER %r, falling back to the local model", provider)
    return KaseLocalClient()
