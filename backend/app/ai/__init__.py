"""LLM layer.

The model behind this layer is our own: ``KaseLocalClient`` talks to the KASE
Bond AI inference service in ``ai/inference``, which serves weights we trained
on a dataset we built. ``OpenAICompatibleClient`` remains for the opt-in
comparison mode only (``AI_PROVIDER=external``).

Scope, enforced by design:

* allowed - rephrasing deterministic explanations, summarising documents,
  parsing free-text search intent, choosing what to show;
* forbidden - producing any number that appears in a metric, score or
  projection. The engine calculates, the model only talks.
"""

from app.ai.base import ChatMessage, LLMClient, LLMResponse, NullLLMClient
from app.ai.local_client import KaseLocalClient
from app.ai.openai_compatible import OpenAICompatibleClient
from app.ai.factory import get_llm_client

__all__ = [
    "ChatMessage",
    "KaseLocalClient",
    "LLMClient",
    "LLMResponse",
    "NullLLMClient",
    "OpenAICompatibleClient",
    "get_llm_client",
]
