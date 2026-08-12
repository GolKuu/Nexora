"""Provider-agnostic chat interface."""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(slots=True)
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@dataclass(slots=True)
class LLMResponse:
    content: str
    model: str | None = None
    provider: str | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    latency_ms: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.content)


class LLMClient(abc.ABC):
    provider: str = "abstract"

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    async def aclose(self) -> None:
        return None


class NullLLMClient(LLMClient):
    """Used when no key is configured: the app degrades, it does not break.

    Every AI feature has a deterministic fallback, so an unavailable model
    costs polish, never correctness.
    """

    provider = "null"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            content="",
            provider=self.provider,
            error="LLM is not configured (OPENAI_API_KEY is empty).",
        )
