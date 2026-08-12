"""Client for any OpenAI-compatible /chat/completions endpoint.

Verified shape works with OpenAI, Azure OpenAI gateways, Anthropic-compatible
proxies, Qwen/DashScope compatible mode, Together, Groq, Ollama and vLLM.
Switching provider is a change of AI_BASE_URL + AI_MODEL, nothing else.
"""

from __future__ import annotations

import time

import httpx

from app.ai.base import ChatMessage, LLMClient, LLMResponse
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class OpenAICompatibleClient(LLMClient):
    provider = "openai_compatible"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ):
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.base_url = (base_url or settings.AI_BASE_URL).rstrip("/")
        self.model = model or settings.AI_MODEL
        self._client = httpx.AsyncClient(
            timeout=timeout or settings.AI_TIMEOUT,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens or settings.AI_MAX_TOKENS,
        }
        started = time.perf_counter()
        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions", json=payload
            )
        except Exception as exc:
            logger.warning("LLM request failed: %s", exc)
            return LLMResponse(
                content="", model=self.model, provider=self.provider, error=str(exc)
            )
        latency = (time.perf_counter() - started) * 1000

        if response.status_code >= 400:
            return LLMResponse(
                content="",
                model=self.model,
                provider=self.provider,
                latency_ms=latency,
                error=f"HTTP {response.status_code}: {response.text[:500]}",
            )
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            return LLMResponse(
                content="",
                model=self.model,
                provider=self.provider,
                latency_ms=latency,
                error=f"Unexpected response shape: {exc}",
            )
        usage = data.get("usage") or {}
        return LLMResponse(
            content=content.strip(),
            model=data.get("model") or self.model,
            provider=self.provider,
            tokens_prompt=usage.get("prompt_tokens"),
            tokens_completion=usage.get("completion_tokens"),
            latency_ms=latency,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
