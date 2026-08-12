from __future__ import annotations

from functools import lru_cache

from app.ai.base import LLMClient, NullLLMClient
from app.ai.openai_compatible import OpenAICompatibleClient
from app.core.config import settings


@lru_cache
def get_llm_client() -> LLMClient:
    if not settings.AI_ENABLED or not settings.OPENAI_API_KEY:
        return NullLLMClient()
    return OpenAICompatibleClient()
